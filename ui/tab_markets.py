from __future__ import annotations

import datetime
import random

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from data.quality import DataSource
from ui.styles import (
    C_ACCENT,
    C_BORDER,
    C_CARD,
    C_CONV,
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

# ── mock signal data ────────────────────────────────────────────────────────────
_MOCK_SIGNALS = [
    ("BDI",       "MOMENTUM",        "HIGH",     "LONG",  "+4.2%", "2h ago",  "BDI printed 3-week high; capesize led"),
    ("SCFI",      "MEAN REVERSION",  "MODERATE", "SHORT", "-1.8%", "4h ago",  "SCFI overbought on spot-contract spread"),
    ("WCI",       "MACRO OVERLAY",   "HIGH",     "LONG",  "+2.9%", "1h ago",  "Surge in booked TEUs ex-Shanghai"),
    ("CCFI",      "MOMENTUM",        "MODERATE", "LONG",  "+1.4%", "6h ago",  "CCFI holding above 200-day MA"),
    ("ZIM",       "BDI DIVERGENCE",  "HIGH",     "SHORT", "-3.1%", "30m ago", "Stock lagging BDI rally; fade opportunity"),
    ("MATX",      "MOMENTUM",        "MODERATE", "LONG",  "+2.2%", "3h ago",  "Trans-Pacific volume uptick"),
    ("SBLK",      "MEAN REVERSION",  "LOW",      "LONG",  "+0.6%", "5h ago",  "Panamax spot mean reversion signal"),
    ("GOGL",      "MACRO OVERLAY",   "MODERATE", "SHORT", "-2.4%", "2h ago",  "Iron ore demand softening"),
    ("DAC",       "MOMENTUM",        "HIGH",     "LONG",  "+5.1%", "1h ago",  "Containership charter rates accelerating"),
    ("FBX01",     "BDI DIVERGENCE",  "HIGH",     "LONG",  "+3.8%", "45m ago", "Trans-Pac spot diverged from futures"),
    ("FBX11",     "MOMENTUM",        "MODERATE", "SHORT", "-1.2%", "3h ago",  "Europe-Asia backhaul softening"),
    ("CAPESIZE",  "MOMENTUM",        "HIGH",     "LONG",  "+6.3%", "1h ago",  "Capesize TCE printing multi-month high"),
    ("PANAMAX",   "MEAN REVERSION",  "MODERATE", "LONG",  "+1.7%", "2h ago",  "Panamax reverting from oversold"),
    ("SUPRAMAX",  "MACRO OVERLAY",   "LOW",      "SHORT", "-0.9%", "7h ago",  "Minor bulker demand soft amid PMI miss"),
    ("HANDYSIZE", "MOMENTUM",        "LOW",      "LONG",  "+0.4%", "8h ago",  "Modest handysize improvement"),
    ("OIL_USO",   "MACRO OVERLAY",   "HIGH",     "SHORT", "-2.7%", "1h ago",  "Oil selloff pressuring tanker margins"),
    ("DXY",       "MACRO OVERLAY",   "MODERATE", "SHORT", "-1.1%", "2h ago",  "Dollar weakening supports commodity trade"),
    ("SPY",       "MACRO OVERLAY",   "MODERATE", "LONG",  "+0.8%", "4h ago",  "Risk-on supports shipping equities"),
    ("GOLD",      "MEAN REVERSION",  "LOW",      "SHORT", "-0.5%", "6h ago",  "Gold overbought; risk appetite improving"),
    ("CMRE",      "BDI DIVERGENCE",  "HIGH",     "LONG",  "+4.7%", "2h ago",  "CMRE charter backlog expanding"),
    ("EGLE",      "MOMENTUM",        "MODERATE", "LONG",  "+2.1%", "3h ago",  "Dry bulk equity momentum intact"),
    ("GNK",       "MEAN REVERSION",  "MODERATE", "LONG",  "+1.9%", "5h ago",  "GNK cheap vs. capesize TCE"),
    ("VLCC",      "MACRO OVERLAY",   "HIGH",     "SHORT", "-3.4%", "1h ago",  "VLCC rates rolling over on OPEC cuts"),
    ("AFRAMAX",   "BDI DIVERGENCE",  "MODERATE", "LONG",  "+2.6%", "3h ago",  "Aframax outperforming VLCC; structural"),
    ("MR_TANKER", "MOMENTUM",        "LOW",      "LONG",  "+0.7%", "6h ago",  "Product tanker rates stable"),
    ("BALTIC_C5", "MOMENTUM",        "HIGH",     "LONG",  "+5.8%", "30m ago", "C5 route flush with iron ore cargo"),
    ("BALTIC_C3", "MEAN REVERSION",  "MODERATE", "SHORT", "-1.6%", "4h ago",  "C3 route vol compressing"),
]

_ROUTES = [
    "Shanghai → LA",
    "Shanghai → Rotterdam",
    "Rotterdam → NY",
    "Singapore → Rotterdam",
    "Houston → Rotterdam",
    "Dubai → Shanghai",
    "Santos → Rotterdam",
    "Dampier → Qingdao",
    "Richards Bay → Qingdao",
    "New Orleans → Yokohama",
    "Durban → Rotterdam",
    "Corpus Christi → Rotterdam",
]

_CONVICTION_COLOR: dict[str, str] = {
    "HIGH":     C_HIGH,
    "MODERATE": C_MOD,
    "LOW":      C_TEXT3,
}

_TYPE_COLOR: dict[str, str] = {
    "MOMENTUM":       C_ACCENT,
    "MEAN REVERSION": C_HIGH,
    "BDI DIVERGENCE": C_MOD,
    "MACRO OVERLAY":  C_CONV,
}


# ── Cell formatters ──────────────────────────────────────────────────────────
def _mono(value: str, color: str = C_TEXT, weight: int = 500) -> str:
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-weight:{weight};font-size:0.82rem;">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT, weight: int = 500) -> str:
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};font-size:0.82rem;">{value}</span>'
    )


def _direction_cell(direction: str) -> str:
    if direction == "LONG":
        return _sans(f"↑ {direction}", color=C_HIGH, weight=600)
    return _sans(f"↓ {direction}", color=C_LOW, weight=600)


def _change_cell(change: str) -> str:
    color = C_HIGH if change.startswith("+") else C_LOW
    return _mono(change, color=color, weight=600)


def _posture_from_signals(signals: list) -> tuple[str, str]:
    longs  = sum(1 for s in signals if s[3] == "LONG")
    shorts = sum(1 for s in signals if s[3] == "SHORT")
    ratio  = longs / max(longs + shorts, 1)
    if ratio >= 0.65:
        return "BULLISH", C_HIGH
    if ratio <= 0.35:
        return "BEARISH", C_LOW
    if 0.45 <= ratio <= 0.55:
        return "NEUTRAL", C_TEXT2
    return "MIXED", C_MOD


# ── section 1: signal KPI hero ─────────────────────────────────────────────────

def _render_signal_hero(signals: list) -> None:
    try:
        total = len(signals)
        high_n = sum(1 for s in signals if s[2] == "HIGH")
        mod_n  = sum(1 for s in signals if s[2] == "MODERATE")
        low_n  = sum(1 for s in signals if s[2] == "LOW")

        posture, posture_color = _posture_from_signals(signals)

        rng = random.Random(42)
        new_today = rng.randint(3, 18)
        trend_sign = "↑" if new_today > 8 else "↓"
        trend_color = C_HIGH if new_today > 8 else C_LOW

        metric_card_row([
            {"label": "MARKET POSTURE", "value": posture,   "accent": posture_color,
             "delta": f"{trend_sign} {new_today} new vs yesterday", "delta_color": trend_color},
            {"label": "TOTAL SIGNALS",  "value": f"{total}", "accent": C_ACCENT,
             "sublabel": "Live + mock blended"},
            {"label": "HIGH CONVICTION",     "value": f"{high_n}", "accent": C_HIGH},
            {"label": "MODERATE CONVICTION", "value": f"{mod_n}",  "accent": C_MOD},
            {"label": "LOW CONVICTION",      "value": f"{low_n}",  "accent": C_TEXT3},
        ], columns=5)
        st.markdown(
            source_footer([DataSource.demo("Signal Posture KPIs")]),
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"signal hero error: {exc}")
        st.warning("Signal hero unavailable.")


# ── section 2: signal table ─────────────────────────────────────────────────────

def _render_signal_table(signals: list) -> None:
    try:
        headers = ["Instrument", "Signal", "Conviction", "Direction", "Change", "Time", "Basis"]
        rows: list[list[str]] = []
        for sig in signals:
            instrument, signal_type, conviction, direction, change, time_ago, basis = sig
            rows.append([
                _mono(instrument, weight=700),
                _sans(signal_type, color=C_ACCENT, weight=600),
                badge(conviction, _CONVICTION_COLOR.get(conviction, C_TEXT3)),
                _direction_cell(direction),
                _change_cell(change),
                _sans(time_ago, color=C_TEXT3),
                _sans(basis, color=C_TEXT3),
            ])
        wsj_market_table(headers, rows)
        st.markdown(
            source_footer([DataSource.demo("Mock Signal Intelligence")]),
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"signal table error: {exc}")
        st.warning("Signal table unavailable.")


# ── section 3: multi-index performance chart ────────────────────────────────────

def _render_multi_index_chart() -> None:
    try:
        rng = np.random.default_rng(7)
        days = pd.date_range(end=pd.Timestamp.today(), periods=90, freq="B")

        def _gen_index(vol: float) -> np.ndarray:
            returns = rng.normal(0, vol, len(days))
            prices  = 100 * np.cumprod(1 + returns)
            prices[0] = 100.0
            return prices

        series = {
            "BDI":  (_gen_index(0.018), C_ACCENT),
            "WCI":  (_gen_index(0.014), C_HIGH),
            "SCFI": (_gen_index(0.012), C_MOD),
            "CCFI": (_gen_index(0.010), C_CONV),
        }

        fig = go.Figure()
        for name, (vals, color) in series.items():
            fig.add_trace(go.Scatter(
                x=list(days),
                y=vals,
                name=name,
                line=dict(color=color, width=1.5),
                hovertemplate=f"<b>{name}</b>: %{{y:.1f}}<extra></extra>",
            ))

        fig.add_hline(y=100, line_dash="dot", line_color=C_BORDER, line_width=1)

        apply_dark_layout(
            fig,
            height=320,
            margin=dict(l=0, r=0, t=10, b=0),
            legend=dict(
                orientation="h",
                yanchor="bottom", y=1.02,
                xanchor="right",  x=1,
                bgcolor="rgba(0,0,0,0)",
            ),
            xaxis=dict(tickformat="%b %d"),
            hovermode="x unified",
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        st.markdown(
            source_footer([DataSource.demo("Synthetic Index Series · 90-day")]),
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"multi-index chart error: {exc}")
        st.warning("Multi-index chart unavailable.")


# ── section 4: freight rate heatmap ────────────────────────────────────────────

def _render_freight_heatmap(freight_data) -> None:
    try:
        rng = np.random.default_rng(99)
        n_weeks = 7
        today = pd.Timestamp.today()
        week_labels = [(today - pd.Timedelta(weeks=(n_weeks - 1 - i))).strftime("W%W %b %d") for i in range(n_weeks)]

        z = rng.normal(0.02, 0.06, (len(_ROUTES), n_weeks)) * 100  # percent
        text = [[f"{v:+.1f}%" for v in row] for row in z]

        colorscale = [[0.0, C_LOW], [0.5, "#1a1e2a"], [1.0, C_HIGH]]
        fig = go.Figure(go.Heatmap(
            z=z,
            x=week_labels,
            y=_ROUTES,
            text=text,
            texttemplate="%{text}",
            textfont=dict(family="JetBrains Mono", size=11, color=C_TEXT),
            colorscale=colorscale,
            zmin=-8, zmax=8,
            showscale=True,
            colorbar=dict(
                title=dict(text="% change", font=dict(color=C_TEXT2, size=10)),
                tickfont=dict(color=C_TEXT2, size=10),
                bgcolor=C_CARD,
                bordercolor=C_BORDER,
                borderwidth=1,
                len=0.85,
            ),
            hovertemplate="<b>%{y}</b><br>%{x}: %{text}<extra></extra>",
        ))
        apply_dark_layout(
            fig,
            height=360,
            margin=dict(l=10, r=10, t=10, b=10),
            xaxis=dict(showgrid=False, zeroline=False, side="top"),
            yaxis=dict(showgrid=False, autorange="reversed"),
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        st.markdown(
            source_footer([DataSource.demo("Synthetic Freight Rate Heatmap")]),
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"freight heatmap error: {exc}")
        st.warning("Freight heatmap unavailable.")


# ── section 5: correlation matrix ──────────────────────────────────────────────

def _render_correlation_matrix() -> None:
    try:
        rng = np.random.default_rng(17)
        shipping_indices = ["BDI", "WCI", "SCFI", "CCFI"]
        macro_assets     = ["S&P 500", "Gold", "USD Index", "Oil (WTI)"]

        corr_mock = np.array([
            [ 0.42,  0.18, -0.31,  0.55],
            [ 0.38,  0.22, -0.27,  0.49],
            [ 0.51,  0.09, -0.38,  0.44],
            [ 0.46,  0.14, -0.29,  0.51],
        ])
        corr_mock = np.clip(corr_mock + rng.normal(0, 0.04, corr_mock.shape), -1, 1)

        text = [[f"{v:+.2f}" for v in row] for row in corr_mock]
        colorscale = [[0.0, C_LOW], [0.5, "#1a1e2a"], [1.0, C_HIGH]]

        fig = go.Figure(go.Heatmap(
            z=corr_mock,
            x=macro_assets,
            y=shipping_indices,
            text=text,
            texttemplate="%{text}",
            textfont=dict(family="JetBrains Mono", size=12, color=C_TEXT),
            colorscale=colorscale,
            zmin=-1, zmax=1,
            showscale=True,
            colorbar=dict(
                title=dict(text="ρ", font=dict(color=C_TEXT2, size=11)),
                tickfont=dict(color=C_TEXT2, size=10),
                bgcolor=C_CARD,
                bordercolor=C_BORDER,
                borderwidth=1,
                len=0.85,
            ),
            hovertemplate="<b>%{y} vs %{x}</b><br>ρ = %{text}<extra></extra>",
        ))
        apply_dark_layout(
            fig,
            height=300,
            margin=dict(l=10, r=10, t=10, b=10),
            xaxis=dict(showgrid=False, zeroline=False, side="top"),
            yaxis=dict(showgrid=False, autorange="reversed"),
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        st.markdown(
            source_footer([DataSource.demo("Synthetic Correlation Matrix")]),
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"correlation matrix error: {exc}")
        st.warning("Correlation matrix unavailable.")


# ── section 6: conviction distribution chart ────────────────────────────────────

def _render_conviction_chart(signals: list) -> None:
    try:
        counts = {
            "HIGH":     sum(1 for s in signals if s[2] == "HIGH"),
            "MODERATE": sum(1 for s in signals if s[2] == "MODERATE"),
            "LOW":      sum(1 for s in signals if s[2] == "LOW"),
        }
        labels = list(counts.keys())
        values = list(counts.values())
        colors = [C_HIGH, C_MOD, C_TEXT3]

        fig = go.Figure(go.Bar(
            y=labels,
            x=values,
            orientation="h",
            marker=dict(color=colors, line=dict(width=0)),
            text=[str(v) for v in values],
            textposition="outside",
            textfont=dict(family="JetBrains Mono", size=12, color=C_TEXT2),
            hovertemplate="<b>%{y}</b>: %{x} signals<extra></extra>",
        ))
        apply_dark_layout(
            fig,
            height=200,
            margin=dict(l=0, r=40, t=10, b=0),
            showlegend=False,
            xaxis=dict(range=[0, max(values) * 1.25]),
            yaxis=dict(showgrid=False),
            barmode="group",
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        st.markdown(
            source_footer([DataSource.demo("Signal Conviction Counts")]),
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"conviction chart error: {exc}")
        st.warning("Conviction chart unavailable.")


# ── section 7: signal type breakdown ───────────────────────────────────────────

def _render_type_breakdown(signals: list) -> None:
    try:
        type_counts: dict[str, int] = {}
        for sig in signals:
            t = sig[1]
            type_counts[t] = type_counts.get(t, 0) + 1

        total_sigs = max(len(signals), 1)
        rows = []
        for stype, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            pct   = count / total_sigs * 100
            bar_c = _TYPE_COLOR.get(stype, C_TEXT3)
            bar = (
                f'<div class="progress-bar-custom">'
                f'<div class="progress-bar-fill" style="width:{pct:.1f}%;background:{bar_c};"></div>'
                f'</div>'
            )
            rows.append([
                _sans(stype, color=C_TEXT2, weight=600),
                _mono(f"{count} ({pct:.0f}%)", color=C_TEXT3),
                bar,
            ])
        wsj_market_table(["Signal Type", "Count", "Share"], rows)
        st.markdown(
            source_footer([DataSource.demo("Mock Signal Distribution")]),
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"signal type breakdown error: {exc}")


# ── main entry point ─────────────────────────────────────────────────────────────

def render(stock_data, macro_data, insights, freight_data=None) -> None:
    """Institutional markets & signals dashboard — WSJ editorial style."""
    # resolve signals: prefer live insights, fallback to mock
    signals: list = _MOCK_SIGNALS
    try:
        if insights and hasattr(insights, "__iter__"):
            live = []
            for item in insights:
                try:
                    sig = (
                        str(getattr(item, "ticker",    item.get("ticker",    "UNK"))),
                        str(getattr(item, "signal",    item.get("signal",    "MOMENTUM"))),
                        str(getattr(item, "conviction",item.get("conviction","MODERATE"))).upper(),
                        str(getattr(item, "direction", item.get("direction", "LONG"))).upper(),
                        str(getattr(item, "change",    item.get("change",    "—"))),
                        str(getattr(item, "time_ago",  item.get("time_ago",  "—"))),
                        str(getattr(item, "basis",     item.get("basis",     "—"))),
                    )
                    live.append(sig)
                except Exception:
                    pass
            if len(live) >= 5:
                signals = live
    except Exception as exc:
        logger.debug(f"insights parse skipped: {exc}")

    page_header(
        title="Markets & Signals Dashboard",
        subtitle="Live signal monitoring across shipping indices, routes, and equities.",
        icon="📊",
        badge_text="Signal Intelligence",
        badge_color=C_ACCENT,
    )

    try:
        _render_signal_hero(signals)
    except Exception as exc:
        logger.error(f"signal hero section failed: {exc}")

    section_divider()
    try:
        section_header(
            "Signal Intelligence Table",
            f"{len(signals)} active signals · sortable by conviction",
        )
        _render_signal_table(signals)
    except Exception as exc:
        logger.error(f"signal table section failed: {exc}")

    section_divider()
    try:
        section_header(
            "Multi-Index Performance",
            "BDI · WCI · SCFI · CCFI · indexed to 100 · trailing 90 trading days",
        )
        _render_multi_index_chart()
    except Exception as exc:
        logger.error(f"multi-index section failed: {exc}")

    section_divider()
    try:
        col_heat, col_corr = st.columns([3, 2], gap="medium")
        with col_heat:
            try:
                section_header(
                    "Freight Rate Heatmap",
                    "12 trade routes · weekly rate change · green = up, red = down",
                )
                _render_freight_heatmap(freight_data)
            except Exception as exc:
                logger.error(f"freight heatmap column failed: {exc}")
        with col_corr:
            try:
                section_header(
                    "Correlation Matrix",
                    "Shipping indices vs macro assets · 90-day rolling",
                )
                _render_correlation_matrix()
            except Exception as exc:
                logger.error(f"correlation matrix column failed: {exc}")
    except Exception as exc:
        logger.error(f"layout columns failed: {exc}")

    section_divider()
    try:
        col_conv, col_meta = st.columns([2, 3], gap="medium")
        with col_conv:
            section_header("Conviction Distribution", "Signal count by confidence tier.")
            _render_conviction_chart(signals)
        with col_meta:
            section_header("Signal Type Breakdown", "Distribution by signal methodology.")
            _render_type_breakdown(signals)
    except Exception as exc:
        logger.error(f"conviction section failed: {exc}")

    section_divider(label=datetime.datetime.now().strftime("Markets & Signals · Last updated %Y-%m-%d %H:%M"))
