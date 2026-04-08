"""tab_bellwethers.py — WSJ-style Trade Bellwether Indicators & Earnings Calendar.

Displays:
  1. Composite bellwether score with editorial narrative
  2. Individual indicator breakdown table
  3. Yield curve analysis with shipping implications
  4. Upcoming shipping earnings calendar
"""
from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st
from loguru import logger

# ── WSJ Palette ──────────────────────────────────────────────────────────────
C_BG      = "#0c0e14"
C_SURFACE = "#12151e"
C_CARD    = "#181c28"
C_RULE    = "rgba(232,230,225,0.12)"
C_HIGH    = "#2e9e6e"
C_MOD     = "#c9962b"
C_LOW     = "#c0392b"
C_ACCENT  = "#3572b0"
C_TEXT    = "#e8e6e1"
C_TEXT2   = "#9a968e"
C_TEXT3   = "#6b6760"


def _rgba(h: str, a: float) -> str:
    try:
        h2 = h.lstrip("#")
        r, g, b = int(h2[0:2], 16), int(h2[2:4], 16), int(h2[4:6], 16)
        return f"rgba({r},{g},{b},{a})"
    except Exception:
        return f"rgba(255,255,255,{a})"


def _score_color(s: float) -> str:
    if s >= 0.65: return C_HIGH
    if s >= 0.45: return C_MOD
    return C_LOW


def render(macro_data=None, **kwargs) -> None:
    """Render the Trade Bellwethers dashboard."""
    try:
        from processing.trade_bellwethers import (
            compute_bellwether_score,
            compute_earnings_calendar,
            compute_yield_curve_analysis,
        )
    except ImportError as e:
        st.error(f"Trade bellwethers module not available: {e}")
        return

    macro_data = macro_data or {}

    # ── 1. Composite Bellwether Score ────────────────────────────────────────
    bell = compute_bellwether_score(macro_data)
    score = bell["composite_score"]
    label = bell["composite_label"]
    sc = _score_color(score)

    st.markdown(f"""
    <div style="margin-bottom:24px">
        <div style="border-top:2px solid {C_TEXT};padding-top:10px;margin-bottom:14px">
            <div style="font-family:'Libre Baskerville',Georgia,serif;font-size:1.3rem;
                        font-weight:700;color:{C_TEXT}">Trade Bellwether Index</div>
            <div style="font-family:'Libre Franklin',sans-serif;font-size:0.72rem;
                        color:{C_TEXT3};margin-top:2px">
                Composite leading indicator for global shipping demand</div>
        </div>

        <div style="display:flex;gap:24px;align-items:flex-start;flex-wrap:wrap">
            <!-- Score box -->
            <div style="border:1px solid {C_RULE};border-top:2px solid {sc};
                        border-radius:0 0 6px 6px;padding:20px 28px;background:{C_CARD};
                        text-align:center;min-width:160px">
                <div style="font-family:JetBrains Mono,monospace;font-size:2.4rem;
                            font-weight:700;color:{sc};line-height:1">{score:.0%}</div>
                <div style="font-family:'Libre Franklin',sans-serif;font-size:0.68rem;
                            font-weight:700;color:{C_TEXT3};text-transform:uppercase;
                            letter-spacing:0.06em;margin-top:4px">Composite Score</div>
                <div style="margin-top:8px">
                    <span style="background:{_rgba(sc,0.08)};color:{sc};
                                 border:1px solid {_rgba(sc,0.2)};
                                 padding:3px 10px;border-radius:3px;font-size:0.7rem;
                                 font-weight:700;font-family:'Libre Franklin',sans-serif">
                        {label.upper()}</span>
                </div>
            </div>

            <!-- Narrative -->
            <div style="flex:1;min-width:280px">
                <div style="font-family:'Libre Baskerville',Georgia,serif;font-size:1.05rem;
                            font-weight:700;color:{C_TEXT};line-height:1.3;margin-bottom:8px">
                    {'Bullish signals dominate leading indicators' if score >= 0.65 else
                     ('Mixed signals warrant cautious positioning' if score >= 0.45 else
                      'Bearish undertones in economic bellwethers')}
                </div>
                <div style="font-family:'Libre Franklin',sans-serif;font-size:0.86rem;
                            color:{C_TEXT2};line-height:1.65">
                    {bell.get('narrative', 'Narrative unavailable.')}
                </div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── 2. Indicator Breakdown ───────────────────────────────────────────────
    indicators = bell.get("indicators", {})
    if indicators:
        st.markdown(f"""
        <div style="border-top:2px solid {C_TEXT};padding-top:10px;margin-bottom:14px;margin-top:24px">
            <div style="font-family:'Libre Baskerville',Georgia,serif;font-size:1rem;
                        font-weight:700;color:{C_TEXT}">Indicator Breakdown</div>
        </div>
        """, unsafe_allow_html=True)

        # WSJ table
        hdr = "<tr>"
        for h in ["Indicator", "Reading", "Score", "Signal"]:
            align = "left" if h == "Indicator" else "right"
            hdr += f'<th style="text-align:{align};font-family:Libre Franklin,sans-serif;font-size:0.64rem;font-weight:700;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.08em;padding:6px 12px;border-bottom:2px solid {C_TEXT}">{h}</th>'
        hdr += "</tr>"

        tbody = ""
        for key, ind in indicators.items():
            raw = ind.get("raw")
            raw_str = f'{raw:.2f}' if raw is not None else "--"
            unit = ind.get("unit", "")
            if unit:
                raw_str += f" {unit}"
            ind_score = ind.get("score", 0.5)
            ind_color = _score_color(ind_score)
            interp = ind.get("interpretation", "")

            # Score bar
            pct = int(ind_score * 100)
            bar = f"""
            <div style="display:flex;align-items:center;gap:6px;justify-content:flex-end">
                <div style="width:60px;height:3px;background:{_rgba(C_TEXT,0.06)};border-radius:2px;overflow:hidden">
                    <div style="height:100%;width:{pct}%;background:{_rgba(ind_color,0.6)};border-radius:2px"></div>
                </div>
                <span style="font-family:JetBrains Mono,monospace;font-size:0.78rem;
                             font-weight:700;color:{ind_color};min-width:32px;text-align:right">{pct}%</span>
            </div>
            """

            tbody += f"""
            <tr>
                <td style="font-family:Libre Franklin,sans-serif;font-size:0.82rem;font-weight:600;
                           color:{C_TEXT};padding:10px 12px;border-bottom:1px solid {C_RULE}">
                    {ind.get('label', key)}</td>
                <td style="text-align:right;font-family:JetBrains Mono,monospace;font-size:0.82rem;
                           color:{C_TEXT2};padding:10px 12px;border-bottom:1px solid {C_RULE}">{raw_str}</td>
                <td style="text-align:right;padding:10px 12px;border-bottom:1px solid {C_RULE}">{bar}</td>
                <td style="text-align:right;font-family:Libre Franklin,sans-serif;font-size:0.78rem;
                           color:{ind_color};padding:10px 12px;border-bottom:1px solid {C_RULE}">{interp}</td>
            </tr>
            """

        st.markdown(f"""
        <table style="width:100%;border-collapse:collapse">
            <thead>{hdr}</thead>
            <tbody>{tbody}</tbody>
        </table>
        """, unsafe_allow_html=True)

    # ── 3. Yield Curve Analysis ──────────────────────────────────────────────
    yc = compute_yield_curve_analysis(macro_data)
    if yc.get("curve_points"):
        st.markdown(f"""
        <div style="border-top:2px solid {C_TEXT};padding-top:10px;margin-bottom:14px;margin-top:28px">
            <div style="font-family:'Libre Baskerville',Georgia,serif;font-size:1rem;
                        font-weight:700;color:{C_TEXT}">Treasury Yield Curve</div>
            <div style="font-family:'Libre Franklin',sans-serif;font-size:0.72rem;
                        color:{C_TEXT3};margin-top:2px">
                Curve shape: <span style="font-weight:700;color:{C_TEXT}">{yc['shape']}</span></div>
        </div>
        """, unsafe_allow_html=True)

        # Render yield curve chart
        try:
            import plotly.graph_objects as go
            fig = go.Figure()
            tenors = [p["tenor"] for p in yc["curve_points"]]
            yields = [p["yield"] for p in yc["curve_points"]]
            fig.add_trace(go.Scatter(
                x=tenors, y=yields, mode="lines+markers",
                line=dict(color=C_ACCENT, width=2),
                marker=dict(size=6, color=C_ACCENT),
                name="Current Curve",
            ))
            fig.update_layout(
                paper_bgcolor=C_BG,
                plot_bgcolor=C_SURFACE,
                font=dict(color=C_TEXT2, family="Libre Franklin, sans-serif", size=12),
                title=dict(
                    text="US Treasury Yield Curve",
                    font=dict(family="Libre Baskerville, Georgia, serif", size=14, color=C_TEXT),
                    x=0.01,
                ),
                height=320,
                margin=dict(l=24, r=24, t=40, b=24),
                xaxis=dict(
                    gridcolor="rgba(232,230,225,0.04)",
                    tickfont=dict(color=C_TEXT3, size=11),
                    title="Maturity",
                ),
                yaxis=dict(
                    gridcolor="rgba(232,230,225,0.04)",
                    tickfont=dict(color=C_TEXT3, size=11),
                    title="Yield (%)",
                    ticksuffix="%",
                ),
                showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        except Exception as exc:
            logger.warning(f"Yield curve chart failed: {exc}")

        # Implication box
        st.markdown(f"""
        <div style="border-top:2px solid {C_TEXT};border-bottom:1px solid {C_RULE};
                    padding:14px 0;margin:12px 0 20px">
            <div style="font-family:'Libre Baskerville',Georgia,serif;font-size:0.95rem;
                        font-weight:700;color:{C_TEXT};line-height:1.4;font-style:italic">
                {yc.get('implication', '')}</div>
            <div style="font-family:'Libre Franklin',sans-serif;font-size:0.68rem;
                        color:{C_TEXT3};margin-top:6px;text-transform:uppercase;
                        letter-spacing:0.06em">-- Yield Curve Analysis</div>
        </div>
        """, unsafe_allow_html=True)

        # Spread table
        if yc.get("spreads"):
            cols = st.columns(len(yc["spreads"]))
            for col, (spread_name, spread_val) in zip(cols, yc["spreads"].items()):
                with col:
                    sv_color = C_LOW if spread_val < 0 else (C_MOD if spread_val < 0.5 else C_HIGH)
                    st.markdown(f"""
                    <div style="border:1px solid {C_RULE};border-top:2px solid {sv_color};
                                border-radius:0 0 6px 6px;padding:12px 14px;background:{C_CARD};text-align:center">
                        <div style="font-family:JetBrains Mono,monospace;font-size:1.3rem;
                                    font-weight:700;color:{sv_color};line-height:1">{spread_val:+.2f}%</div>
                        <div style="font-family:'Libre Franklin',sans-serif;font-size:0.64rem;
                                    font-weight:700;color:{C_TEXT3};text-transform:uppercase;
                                    letter-spacing:0.06em;margin-top:3px">{spread_name}</div>
                    </div>
                    """, unsafe_allow_html=True)

    # ── 4. Earnings Calendar ─────────────────────────────────────────────────
    calendar = compute_earnings_calendar()
    if calendar:
        st.markdown(f"""
        <div style="border-top:2px solid {C_TEXT};padding-top:10px;margin-bottom:14px;margin-top:28px">
            <div style="font-family:'Libre Baskerville',Georgia,serif;font-size:1rem;
                        font-weight:700;color:{C_TEXT}">Shipping Earnings Calendar</div>
            <div style="font-family:'Libre Franklin',sans-serif;font-size:0.72rem;
                        color:{C_TEXT3};margin-top:2px">
                Next {min(len(calendar), 10)} upcoming earnings reports from tracked shipping companies</div>
        </div>
        """, unsafe_allow_html=True)

        # WSJ-style table
        hdr = "<tr>"
        for h in ["Company", "Ticker", "Sector", "Quarter", "Date", "Days Until"]:
            align = "left" if h in ("Company", "Sector") else "right"
            hdr += f'<th style="text-align:{align};font-family:Libre Franklin,sans-serif;font-size:0.64rem;font-weight:700;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.08em;padding:6px 12px;border-bottom:2px solid {C_TEXT}">{h}</th>'
        hdr += "</tr>"

        tbody = ""
        for evt in calendar[:10]:
            days = evt["days_until"]
            urgency_color = C_LOW if days <= 7 else (C_MOD if days <= 30 else C_TEXT2)
            status_badge = ""
            if evt.get("status") == "This Week":
                status_badge = f' <span style="background:{_rgba(C_LOW,0.08)};color:{C_LOW};border:1px solid {_rgba(C_LOW,0.2)};padding:1px 6px;border-radius:3px;font-size:0.6rem;font-weight:700">THIS WEEK</span>'

            tbody += f"""
            <tr>
                <td style="font-family:Libre Franklin,sans-serif;font-size:0.82rem;font-weight:600;
                           color:{C_TEXT};padding:8px 12px;border-bottom:1px solid {C_RULE}">
                    {evt['company']}{status_badge}</td>
                <td style="text-align:right;font-family:JetBrains Mono,monospace;font-size:0.82rem;
                           font-weight:600;color:{C_ACCENT};padding:8px 12px;border-bottom:1px solid {C_RULE}">
                    {evt['ticker']}</td>
                <td style="font-family:Libre Franklin,sans-serif;font-size:0.78rem;
                           color:{C_TEXT2};padding:8px 12px;border-bottom:1px solid {C_RULE}">
                    {evt['sector']}</td>
                <td style="text-align:right;font-family:Libre Franklin,sans-serif;font-size:0.78rem;
                           color:{C_TEXT2};padding:8px 12px;border-bottom:1px solid {C_RULE}">
                    {evt['quarter']}</td>
                <td style="text-align:right;font-family:JetBrains Mono,monospace;font-size:0.78rem;
                           color:{C_TEXT2};padding:8px 12px;border-bottom:1px solid {C_RULE}">
                    {evt['date_display']}</td>
                <td style="text-align:right;font-family:JetBrains Mono,monospace;font-size:0.82rem;
                           font-weight:600;color:{urgency_color};padding:8px 12px;border-bottom:1px solid {C_RULE}">
                    {days}d</td>
            </tr>
            """

        st.markdown(f"""
        <table style="width:100%;border-collapse:collapse">
            <thead>{hdr}</thead>
            <tbody>{tbody}</tbody>
        </table>
        """, unsafe_allow_html=True)
