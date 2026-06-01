from __future__ import annotations

import datetime
import math
import random

import plotly.graph_objects as go
import streamlit as st

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from utils.helpers import stable_hash
from ui.styles import (
    _hex_to_rgba,
    C_ACCENT,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    apply_dark_layout,
    badge,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    wsj_market_table,
)

# ── Mock data ─────────────────────────────────────────────────────────────────
_ROUTES: list[dict] = [
    {"route": "Asia–Europe",         "rate": 4850, "w": -3.2,  "m":  8.1, "q": 12.4, "y":  22.1},
    {"route": "Transpacific EB",     "rate": 5200, "w":  1.8,  "m":  5.6, "q":  9.8, "y":  31.4},
    {"route": "Transpacific WB",     "rate": 2100, "w": -0.9,  "m": -2.3, "q": -5.1, "y":  -8.2},
    {"route": "Asia–USGC",           "rate": 5900, "w":  4.1,  "m":  9.3, "q": 14.7, "y":  38.6},
    {"route": "Intra-Asia",          "rate": 1350, "w": -1.4,  "m": -0.8, "q":  1.2, "y":   4.3},
    {"route": "Asia–USEC",           "rate": 6100, "w":  2.7,  "m":  7.2, "q": 11.9, "y":  29.7},
    {"route": "Europe–Asia",         "rate": 3200, "w": -2.1,  "m":  1.4, "q":  3.6, "y":  15.8},
    {"route": "Transatlantic EB",    "rate": 2800, "w":  0.5,  "m":  2.9, "q":  5.4, "y":  10.2},
    {"route": "Transatlantic WB",    "rate": 2450, "w": -0.3,  "m":  1.1, "q":  2.8, "y":   7.5},
    {"route": "Asia–Middle East",    "rate": 2100, "w":  3.3,  "m":  6.8, "q": 10.1, "y":  18.9},
    {"route": "Asia–Latin America",  "rate": 4400, "w":  1.2,  "m":  4.5, "q":  7.3, "y":  24.6},
    {"route": "Europe–USEC",         "rate": 2650, "w": -1.7,  "m":  0.3, "q":  1.9, "y":   9.1},
    {"route": "Asia–Africa",         "rate": 2900, "w":  5.6,  "m": 12.3, "q": 18.4, "y":  42.2},
    {"route": "Asia–Oceania",        "rate": 1900, "w": -0.6,  "m":  0.9, "q":  2.1, "y":   6.4},
    {"route": "Europe–Africa",       "rate": 1700, "w": -2.8,  "m": -3.1, "q": -6.2, "y": -11.4},
    {"route": "Intra-Europe",        "rate": 1100, "w":  0.2,  "m":  0.7, "q":  1.3, "y":   3.8},
    {"route": "Middle East–Europe",  "rate": 2300, "w":  2.9,  "m":  5.1, "q":  8.6, "y":  21.3},
    {"route": "Asia–India",          "rate": 1250, "w": -0.4,  "m":  1.6, "q":  3.4, "y":   8.7},
    {"route": "USEC–Latin America",  "rate": 1800, "w":  0.8,  "m":  2.2, "q":  4.1, "y":  12.6},
    {"route": "Far East–Scandinavia","rate": 5100, "w":  3.5,  "m":  7.9, "q": 13.2, "y":  27.8},
    {"route": "Asia–North Europe",   "rate": 4700, "w": -1.1,  "m":  6.4, "q": 11.1, "y":  20.4},
    {"route": "Red Sea–Med",         "rate": 3600, "w":  7.2,  "m": 14.8, "q": 22.6, "y":  55.3},
]

_CARRIERS = {
    "Asia–Europe":        ["MSC", "Maersk", "CMA CGM"],
    "Transpacific EB":    ["COSCO", "Evergreen", "Yang Ming"],
    "Asia–USGC":          ["MSC", "Hapag-Lloyd", "ONE"],
    "Asia–USEC":          ["MSC", "Maersk", "Evergreen"],
    "Red Sea–Med":        ["MSC", "CMA CGM", "Hapag-Lloyd"],
}

_TRANSIT = {
    "Asia–Europe": 28, "Transpacific EB": 14, "Asia–USGC": 30,
    "Asia–USEC": 18, "Transatlantic EB": 10, "Red Sea–Med": 9,
}

_DRIVERS = [
    {"factor": "Red Sea Rerouting Premium",  "impact": "+$1,200–1,800/TEU", "type": "disrupt",  "dir": "up"},
    {"factor": "Blank Sailings (current)",   "impact": "–8% capacity removed", "type": "supply", "dir": "up"},
    {"factor": "Fleet Utilization",          "impact": "91.4% — near capacity", "type": "supply", "dir": "up"},
    {"factor": "Panama Canal Surcharge",     "impact": "+$400–600/TEU",     "type": "disrupt",  "dir": "up"},
    {"factor": "Newbuild Deliveries (2025)", "impact": "1.8M TEU entering",  "type": "supply",  "dir": "down"},
    {"factor": "Asia Export Volumes",        "impact": "+6.2% YoY",         "type": "demand",   "dir": "up"},
    {"factor": "US Inventory Cycle",         "impact": "Restocking phase",   "type": "demand",   "dir": "up"},
    {"factor": "Chinese New Year Effect",    "impact": "Seasonal pullback Q1","type": "demand",  "dir": "down"},
    {"factor": "EU Demand Softness",         "impact": "–2.1% import volumes","type": "demand",  "dir": "down"},
]

# Mock data sources — every chart/table uses these for source_footer().
ROUTE_SOURCES = [
    {"name": "Synthetic global lane rates",   "kind": "modeled", "quality": "demo"},
    {"name": "Internal forecast / volatility", "kind": "modeled", "quality": "demo"},
]


# ── Tab-local helpers ─────────────────────────────────────────────────────────

def _chg_color(val: float) -> str:
    return C_HIGH if val > 0 else (C_LOW if val < 0 else C_TEXT3)


def _chg_arrow(val: float) -> str:
    return "▲" if val > 0 else ("▼" if val < 0 else "—")


def _pct(val: float) -> str:
    sign = "+" if val > 0 else ""
    return f"{sign}{val:.1f}%"


def _usd(val: float) -> str:
    return f"${val:,.0f}"


def _mono(value: str, color: str = C_TEXT, weight: int = 400) -> str:
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-weight:{weight};font-variant-numeric:tabular-nums;">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT2, weight: int = 400) -> str:
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )


def _get_routes(freight_data, route_results) -> list[dict]:
    """Extract rate rows from live data or fall back to mock."""
    try:
        rows = []
        if freight_data is not None and hasattr(freight_data, "__iter__"):
            for item in freight_data:
                if isinstance(item, dict) and "route" in item and "rate" in item:
                    rows.append(item)
        if route_results is not None and hasattr(route_results, "__iter__"):
            for item in route_results:
                if isinstance(item, dict) and "route" in item and "rate" in item:
                    rows.append(item)
        if rows:
            return rows
    except Exception as exc:
        logger.warning(f"tab_routes: data extraction failed: {exc}")
    return _ROUTES


# ── Section 1: Freight Rate Pulse ─────────────────────────────────────────────

def _section_pulse(routes: list[dict]) -> None:
    section_header(
        "Freight Rate Pulse",
        "Global freight index and weekly gainers / losers across all lanes",
    )
    try:
        avg_rate = sum(r["rate"] for r in routes) / len(routes)
        avg_w    = sum(r.get("w", 0) for r in routes) / len(routes)
        avg_m    = sum(r.get("m", 0) for r in routes) / len(routes)
        avg_y    = sum(r.get("y", 0) for r in routes) / len(routes)

        metric_card_row(
            [
                dict(
                    label="Global Freight Index",
                    value=_usd(avg_rate),
                    sublabel="Average $/TEU (all lanes)",
                    accent=C_ACCENT,
                ),
                dict(
                    label="WoW",
                    value=f"{_chg_arrow(avg_w)} {_pct(avg_w)}",
                    accent=_chg_color(avg_w),
                ),
                dict(
                    label="MoM",
                    value=f"{_chg_arrow(avg_m)} {_pct(avg_m)}",
                    accent=_chg_color(avg_m),
                ),
                dict(
                    label="YoY",
                    value=f"{_chg_arrow(avg_y)} {_pct(avg_y)}",
                    accent=_chg_color(avg_y),
                ),
            ],
            columns=4,
        )

        # Top gainers / losers as a 6-card row (3 green + 3 red), all WoW.
        sorted_w = sorted(routes, key=lambda r: r.get("w", 0), reverse=True)
        gainers  = sorted_w[:3]
        losers   = sorted_w[-3:][::-1]

        metric_card_row(
            [
                dict(
                    label=f"Gainer #{i + 1}",
                    value=_pct(r.get("w", 0)),
                    sublabel=r["route"],
                    accent=C_HIGH,
                )
                for i, r in enumerate(gainers)
            ] + [
                dict(
                    label=f"Loser #{i + 1}",
                    value=_pct(r.get("w", 0)),
                    sublabel=r["route"],
                    accent=C_LOW,
                )
                for i, r in enumerate(losers)
            ],
            columns=6,
        )

        st.markdown(source_footer(ROUTE_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("tab_routes _section_pulse failed")
        st.warning("Pulse metrics unavailable.")


# ── Section 2: Rate League Table ──────────────────────────────────────────────

def _section_league_table(routes: list[dict]) -> None:
    section_header(
        "Rate League Table",
        "Per-lane spot rate, change horizons, 30-day forecast and direction",
    )
    try:
        rows = []
        for r in routes:
            w_v = r.get("w", 0)
            m_v = r.get("m", 0)
            q_v = r.get("q", 0)
            y_v = r.get("y", 0)
            rate = r.get("rate", 0)
            fc30 = rate * (1 + (w_v + m_v) / 2 / 100)
            fc_c = _chg_color(fc30 - rate)
            direction = "UP" if m_v > 1 else ("DOWN" if m_v < -1 else "NEUTRAL")
            dir_c = C_HIGH if direction == "UP" else (C_LOW if direction == "DOWN" else C_TEXT3)
            conf = min(95, max(55, 75 + abs(m_v) * 1.2))

            rows.append([
                _sans(r["route"], color=C_TEXT, weight=600),
                _mono(_usd(rate), color=C_TEXT, weight=700),
                _mono(f"{_chg_arrow(w_v)} {_pct(w_v)}", color=_chg_color(w_v), weight=600),
                _mono(f"{_chg_arrow(m_v)} {_pct(m_v)}", color=_chg_color(m_v), weight=600),
                _mono(f"{_chg_arrow(q_v)} {_pct(q_v)}", color=_chg_color(q_v), weight=600),
                _mono(f"{_chg_arrow(y_v)} {_pct(y_v)}", color=_chg_color(y_v), weight=600),
                _mono(_usd(fc30), color=fc_c, weight=600),
                badge(direction, color=dir_c),
                _mono(f"{conf:.0f}%", color=C_TEXT2),
            ])
        wsj_market_table(
            ["Route", "Rate/TEU", "1W Chg", "1M Chg", "3M Chg", "YoY", "Fcst 30D", "Direction", "Conf%"],
            rows,
        )
        st.markdown(source_footer(ROUTE_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("tab_routes _section_league_table failed")
        st.warning("League table unavailable.")


# ── Section 3: ML Forecast Panel ─────────────────────────────────────────────

def _section_ml_forecast(routes: list[dict], rate_forecasts, forecasts) -> None:
    section_header(
        "ML Forecast Panel",
        "7/30/90-day forecasts across featured lanes plus model quality metrics",
    )
    try:
        featured_routes = routes[:5]
        fc_source = rate_forecasts if isinstance(rate_forecasts, dict) else {}

        # Featured forecasts as a WSJ table — replaces 5 inline forecast cards.
        try:
            fc_rows = []
            for r in featured_routes:
                name  = r["route"]
                cur   = r["rate"]
                d7    = fc_source.get(name, {}).get("d7",  cur * (1 + r.get("w", 0) / 100))
                d30   = fc_source.get(name, {}).get("d30", cur * (1 + r.get("m", 0) / 100))
                d90   = fc_source.get(name, {}).get("d90", cur * (1 + r.get("q", 0) / 100))
                d7p   = (d7  - cur) / cur * 100
                d30p  = (d30 - cur) / cur * 100
                d90p  = (d90 - cur) / cur * 100
                ci_lo = d30 * 0.92
                ci_hi = d30 * 1.08
                conf  = min(95, max(55, 75 + abs(r.get("m", 0)) * 1.5))

                fc_rows.append([
                    _sans(name, color=C_TEXT, weight=700),
                    _mono(_usd(cur), color=C_ACCENT, weight=700),
                    _mono(f"{_chg_arrow(d7p)} {_pct(d7p)}", color=_chg_color(d7p), weight=600),
                    _mono(f"{_chg_arrow(d30p)} {_pct(d30p)}", color=_chg_color(d30p), weight=600),
                    _mono(f"{_chg_arrow(d90p)} {_pct(d90p)}", color=_chg_color(d90p), weight=600),
                    _mono(f"{_usd(ci_lo)} – {_usd(ci_hi)}", color=C_TEXT2),
                    _mono(f"{conf:.0f}%", color=C_MOD),
                ])
            wsj_market_table(
                ["Route", "Current", "7D Fcst", "30D Fcst", "90D Fcst", "30D 95% CI", "Conf%"],
                fc_rows,
            )
        except Exception as exc:
            logger.warning(f"tab_routes ML featured forecasts: {exc}")

        try:
            top10 = routes[:10]
            names = [r["route"].replace("–", "-") for r in top10]
            cur_vals = [r["rate"] for r in top10]
            fc30_vals = [r["rate"] * (1 + r.get("m", 0) / 100) for r in top10]

            fig = go.Figure()
            fig.add_bar(name="Current Rate", x=names, y=cur_vals,
                        marker_color=C_ACCENT, opacity=0.85)
            fig.add_bar(name="30D Forecast", x=names, y=fc30_vals,
                        marker_color=C_MOD, opacity=0.85)
            apply_dark_layout(
                fig,
                barmode="group",
                height=320,
                margin=dict(l=10, r=10, t=30, b=80),
                legend=dict(orientation="h", y=1.08, font=dict(size=11)),
                xaxis=dict(tickangle=-35, tickfont=dict(size=9)),
                yaxis=dict(title="$/TEU", tickprefix="$"),
            )
            st.plotly_chart(fig, use_container_width=True)
        except Exception as exc:
            logger.warning(f"tab_routes ML bar chart: {exc}")

        try:
            model_data = [
                ("XGBoost Ensemble",   0.894, 0.871, 82.3),
                ("LSTM Sequence",      0.876, 0.849, 79.1),
                ("SARIMA Hybrid",      0.812, 0.798, 74.6),
                ("Ridge Regression",   0.741, 0.723, 71.2),
                ("Naive Baseline",     0.601, 0.589, 58.4),
            ]
            rows = []
            for model, r2_train, r2_val, dir_acc in model_data:
                r2c = C_HIGH if r2_val > 0.85 else (C_MOD if r2_val > 0.75 else C_LOW)
                rows.append([
                    _sans(model, color=C_TEXT, weight=600),
                    _mono(f"{r2_train:.3f}", color=C_TEXT2),
                    _mono(f"{r2_val:.3f}", color=r2c, weight=700),
                    _mono(f"{dir_acc:.1f}%", color=_chg_color(dir_acc - 65)),
                ])
            wsj_market_table(["Model", "R² Train", "R² Val", "Dir Acc"], rows)
        except Exception as exc:
            logger.warning(f"tab_routes model quality table: {exc}")

        st.markdown(source_footer(ROUTE_SOURCES), unsafe_allow_html=True)

    except Exception:
        logger.exception("tab_routes _section_ml_forecast failed")
        st.warning("ML Forecast panel unavailable.")


# ── Section 4: Rate Volatility Analysis ──────────────────────────────────────

def _section_volatility(routes: list[dict]) -> None:
    section_header(
        "Rate Volatility Analysis",
        "Rolling 30-day annualized volatility — high values flag disruption risk",
    )
    try:
        # Instance RNG so render is deterministic without mutating the
        # process-wide random module state.
        rng = random.Random(42)
        vols = []
        for r in routes:
            base_vol = abs(r.get("m", 0)) * 0.8 + abs(r.get("w", 0)) * 1.2
            ann_vol  = base_vol * math.sqrt(252 / 30) + rng.uniform(2, 8)
            vols.append({"route": r["route"], "vol": ann_vol})

        vols.sort(key=lambda x: x["vol"], reverse=True)
        names  = [v["route"].replace("–", "-") for v in vols]
        values = [v["vol"] for v in vols]
        colors = [C_LOW if v > 30 else (C_MOD if v > 18 else C_HIGH) for v in values]

        fig = go.Figure(go.Bar(
            x=values, y=names, orientation="h",
            marker_color=colors,
            text=[f"{v:.1f}%" for v in values],
            textposition="outside",
            textfont=dict(size=10, color=C_TEXT2),
        ))
        apply_dark_layout(
            fig,
            height=480,
            margin=dict(l=160, r=60, t=30, b=20),
            xaxis=dict(title="Annualized Volatility (%)", ticksuffix="%"),
            yaxis=dict(tickfont=dict(size=10)),
        )
        st.plotly_chart(fig, use_container_width=True)

        st.caption(
            "Red (>30%) = high disruption risk  |  "
            "Amber (18–30%) = elevated  |  "
            "Green (<18%) = stable"
        )
        st.markdown(source_footer(ROUTE_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("tab_routes _section_volatility failed")
        st.warning("Volatility analysis unavailable.")


# ── Section 5: Seasonal Pattern ───────────────────────────────────────────────

def _section_seasonal() -> None:
    section_header(
        "Seasonal Rate Patterns",
        "Monthly average $/TEU 2020–2025 — peak-season build, CNY dip, COVID surge",
    )
    try:
        years  = list(range(2020, 2026))
        months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

        base = [
            [1800,1400,1600,1700,1900,2100,2400,2600,2500,2300,2100,1950],
            [3200,2800,4100,5200,6800,7900,8500,9200,8800,8100,7200,6500],
            [7800,7200,7600,8100,8400,8200,7800,7500,6900,6200,5500,4800],
            [3200,2900,2600,2300,2100,1900,1750,1800,1850,1950,2100,2200],
            [2400,2100,2600,3100,3600,4200,4800,5100,4900,4500,4100,3800],
            [4200,3700,4500,5100,5600,5900,6200,6500,6300,6000,5700,5400],
        ]

        z    = base
        text = [[f"${v:,}" for v in row] for row in z]

        fig = go.Figure(go.Heatmap(
            z=z, x=months, y=[str(yr) for yr in years],
            text=text, texttemplate="%{text}",
            textfont=dict(size=9, color=C_TEXT),
            colorscale=[
                [0.0, "#1e2330"], [0.25, "#2a3d52"],
                [0.5, C_ACCENT],  [0.75, C_MOD],
                [1.0, C_LOW],
            ],
            showscale=True,
            colorbar=dict(
                title=dict(text="$/TEU", font=dict(color=C_TEXT2, size=10)),
                tickfont=dict(color=C_TEXT2, size=10),
            ),
        ))
        apply_dark_layout(
            fig,
            height=320,
            margin=dict(l=50, r=80, t=20, b=40),
            xaxis=dict(side="top"),
        )
        st.plotly_chart(fig, use_container_width=True)

        # Seasonal annotations as `badge()` chips, no inline div wrapper.
        annotations = [
            ("Chinese New Year dip: Feb",     C_LOW),
            ("Pre-CNY build: Jan",            C_MOD),
            ("Peak season: Aug–Sep",          C_HIGH),
            ("Post-peak slide: Oct–Nov",      C_TEXT3),
            ("2021: pandemic demand surge",   C_LOW),
            ("2023: post-COVID correction",   C_ACCENT),
        ]
        st.markdown(
            " ".join(badge(label, color=color) for label, color in annotations),
            unsafe_allow_html=True,
        )
        st.markdown(source_footer(ROUTE_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("tab_routes _section_seasonal failed")
        st.warning("Seasonal heatmap unavailable.")


# ── Section 6: Rate Drivers ───────────────────────────────────────────────────

def _section_rate_drivers() -> None:
    section_header(
        "Rate Drivers",
        "Supply, demand and disruption forces shaping global lane economics",
    )
    try:
        type_label = {
            "supply":  ("Supply",     C_ACCENT),
            "demand":  ("Demand",     C_MOD),
            "disrupt": ("Disruption", C_LOW),
        }
        rows = []
        for d in _DRIVERS:
            label, group_color = type_label.get(d["type"], ("Other", C_TEXT3))
            arrow_color = C_HIGH if d["dir"] == "up" else C_LOW
            arrow = _chg_arrow(1 if d["dir"] == "up" else -1)
            rows.append([
                badge(label, color=group_color),
                _sans(d["factor"], color=C_TEXT, weight=600),
                _mono(d["impact"], color=C_TEXT2),
                _sans(arrow, color=arrow_color, weight=700),
            ])
        wsj_market_table(
            ["Group", "Factor", "Impact", "Direction"],
            rows,
        )
        st.markdown(source_footer(ROUTE_SOURCES), unsafe_allow_html=True)
    except Exception:
        logger.exception("tab_routes _section_rate_drivers failed")
        st.warning("Rate drivers unavailable.")


# ── Section 7: Route Profile Cards ───────────────────────────────────────────

def _section_route_profiles(routes: list[dict]) -> None:
    section_header(
        "Route Profile Cards",
        "Per-lane 12-month price action, transit time, top carriers and seasonal index",
    )
    try:
        featured = routes[:8]
        for r in featured:
            name = r["route"]
            try:
                with st.expander(f"{name}  —  {_usd(r['rate'])}/TEU  |  {_pct(r.get('m',0))} MoM", expanded=False):
                    col_chart, col_stats = st.columns([3, 2])

                    with col_chart:
                        try:
                            rng_chart = random.Random(stable_hash(name) % 10000)
                            months_back = 12
                            dates = [datetime.date.today() - datetime.timedelta(days=30 * i) for i in range(months_back, 0, -1)]
                            vals  = [r["rate"]]
                            for _ in range(months_back - 1):
                                prev = vals[-1]
                                vals.append(max(500, prev * (1 + rng_chart.uniform(-0.06, 0.07))))
                            vals = list(reversed(vals))

                            fig = go.Figure()
                            fig.add_scatter(
                                x=[str(d) for d in dates], y=vals,
                                mode="lines+markers",
                                line=dict(color=C_ACCENT, width=2),
                                marker=dict(size=4, color=C_ACCENT),
                                fill="tozeroy",
                                fillcolor=_hex_to_rgba(C_ACCENT, 0.09),
                                name="Rate/TEU",
                            )
                            apply_dark_layout(
                                fig,
                                height=200,
                                margin=dict(l=10, r=10, t=10, b=30),
                                showlegend=False,
                                xaxis=dict(tickfont=dict(size=9)),
                                yaxis=dict(tickprefix="$"),
                            )
                            st.plotly_chart(fig, use_container_width=True)
                        except Exception as exc:
                            logger.warning(f"tab_routes route chart {name}: {exc}")

                    with col_stats:
                        transit  = _TRANSIT.get(name, random.randint(10, 35))
                        carriers = _CARRIERS.get(name, ["MSC", "Maersk", "CMA CGM"])
                        hi_52    = r["rate"] * 1.15
                        lo_52    = r["rate"] * 0.82
                        q_chg    = r.get("q", 0)

                        metric_card_row(
                            [
                                dict(
                                    label="52W High",
                                    value=_usd(hi_52),
                                    accent=C_HIGH,
                                ),
                                dict(
                                    label="52W Low",
                                    value=_usd(lo_52),
                                    accent=C_LOW,
                                ),
                                dict(
                                    label="Transit",
                                    value=f"{transit} days",
                                    accent=C_ACCENT,
                                ),
                                dict(
                                    label="3M Change",
                                    value=_pct(q_chg),
                                    accent=_chg_color(q_chg),
                                ),
                            ],
                            columns=2,
                        )

                        st.caption("Top Carriers")
                        st.markdown(
                            " ".join(badge(c, color=C_ACCENT) for c in carriers),
                            unsafe_allow_html=True,
                        )

                        try:
                            rng_season = random.Random(stable_hash(name + "season") % 9999)
                            seasonal_idx = [1.0 + rng_season.uniform(-0.18, 0.18) for _ in range(12)]
                            months_short = ["J","F","M","A","M","J","J","A","S","O","N","D"]
                            fig2 = go.Figure(go.Bar(
                                x=months_short, y=seasonal_idx,
                                marker_color=[C_HIGH if v > 1.05 else (C_LOW if v < 0.95 else C_MOD) for v in seasonal_idx],
                                showlegend=False,
                            ))
                            apply_dark_layout(
                                fig2,
                                height=110,
                                margin=dict(l=5, r=5, t=20, b=20),
                                title="Seasonal Index",
                                xaxis=dict(tickfont=dict(size=8)),
                                yaxis=dict(tickfont=dict(size=8)),
                            )
                            fig2.update_layout(title_font=dict(size=10, color=C_TEXT3), title_x=0.5)
                            st.plotly_chart(fig2, use_container_width=True)
                        except Exception as exc:
                            logger.warning(f"tab_routes seasonal mini {name}: {exc}")

            except Exception as exc:
                logger.warning(f"tab_routes expander {name}: {exc}")

        st.markdown(source_footer(ROUTE_SOURCES), unsafe_allow_html=True)

    except Exception:
        logger.exception("tab_routes _section_route_profiles failed")
        st.warning("Route profiles unavailable.")


# ── Entry point ───────────────────────────────────────────────────────────────

def render(route_results, freight_data, forecasts=None, rate_forecasts=None) -> None:
    """Freight Rate Analytics & ML Forecasting tab."""
    # Lazy import keeps perf_telemetry off the tab-load critical path.
    from engine.perf_telemetry import track_render
    
    with track_render('routes'):
        try:
            page_header(
                title="Freight Rate Analytics",
                subtitle="Real-time rates, ML forecasting, volatility, seasonal patterns and route profiles",
                badge_text="ROUTES",
                badge_color=C_ACCENT,
            )

            routes = _get_routes(freight_data, route_results)

            _section_pulse(routes)
            section_divider("League Table")
            _section_league_table(routes)
            section_divider("Forecasting")
            _section_ml_forecast(routes, rate_forecasts, forecasts)
            section_divider("Volatility & Seasonality")
            _section_volatility(routes)
            _section_seasonal()
            section_divider("Drivers & Profiles")
            _section_rate_drivers()
            _section_route_profiles(routes)

        except Exception:
            logger.exception("tab_routes render failed")
            st.error("Freight Rate tab failed to render.")
