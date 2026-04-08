"""tab_rate_analytics.py — WSJ-style Freight Rate Analytics Dashboard.

Displays rate regime analysis, percentile rankings, volatility metrics,
and spread analysis across shipping routes.
"""
from __future__ import annotations

import streamlit as st
from loguru import logger

# ── WSJ Palette ──────────────────────────────────────────────────────────────
C_BG      = "#0c0e14"
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


def _regime_color(regime: str) -> str:
    if regime in ("Boom", "Above Average"): return C_HIGH
    if regime in ("Normal",): return C_ACCENT
    if regime in ("Below Average",): return C_MOD
    return C_LOW


def render(freight_data=None, route_results=None, **kwargs) -> None:
    """Render freight rate analytics dashboard."""
    try:
        from processing.rate_analytics import compute_rate_regime, compute_rate_spreads
    except ImportError as e:
        st.error(f"Rate analytics module unavailable: {e}")
        return

    freight_data = freight_data or {}

    if not freight_data:
        st.info("No freight data available for rate analytics.")
        return

    # ── 1. Rate Regime Analysis ──────────────────────────────────────────────
    regime_data = compute_rate_regime(freight_data)

    market_regime = regime_data.get("market_regime", "N/A")
    mr_color = C_HIGH if "Bull" in market_regime or "Growth" in market_regime else (
        C_LOW if "Bear" in market_regime or "Contraction" in market_regime else C_MOD)

    st.markdown(f"""
    <div style="margin-bottom:24px">
        <div style="border-top:2px solid {C_TEXT};padding-top:10px;margin-bottom:16px">
            <div style="font-family:'Libre Baskerville',Georgia,serif;font-size:1.35rem;
                        font-weight:700;color:{C_TEXT}">Freight Rate Analytics</div>
            <div style="font-family:'Libre Franklin',sans-serif;font-size:0.78rem;
                        color:{C_TEXT2};margin-top:4px">
                Rate regime detection, percentile ranking, and market spread analysis</div>
        </div>

        <!-- Market regime summary -->
        <div style="display:flex;gap:20px;align-items:center;flex-wrap:wrap;margin-bottom:16px">
            <div style="border:1px solid {C_RULE};border-top:2px solid {mr_color};
                        border-radius:0 0 6px 6px;padding:14px 20px;background:{C_CARD}">
                <div style="font-family:'Libre Franklin',sans-serif;font-size:0.64rem;font-weight:700;
                            color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.06em">Market Regime</div>
                <div style="font-family:'Libre Baskerville',Georgia,serif;font-size:1.2rem;
                            font-weight:700;color:{mr_color};margin-top:2px">{market_regime}</div>
            </div>
            <div style="border:1px solid {C_RULE};border-top:2px solid {C_ACCENT};
                        border-radius:0 0 6px 6px;padding:14px 20px;background:{C_CARD}">
                <div style="font-family:'Libre Franklin',sans-serif;font-size:0.64rem;font-weight:700;
                            color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.06em">Avg Z-Score</div>
                <div style="font-family:JetBrains Mono,monospace;font-size:1.2rem;
                            font-weight:700;color:{C_TEXT};margin-top:2px">{regime_data.get('avg_z_score',0):.2f}</div>
            </div>
            <div style="border:1px solid {C_RULE};border-top:2px solid {C_ACCENT};
                        border-radius:0 0 6px 6px;padding:14px 20px;background:{C_CARD}">
                <div style="font-family:'Libre Franklin',sans-serif;font-size:0.64rem;font-weight:700;
                            color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.06em">Avg Percentile</div>
                <div style="font-family:JetBrains Mono,monospace;font-size:1.2rem;
                            font-weight:700;color:{C_TEXT};margin-top:2px">{regime_data.get('avg_percentile',50):.0f}th</div>
            </div>
            <div style="border:1px solid {C_RULE};border-top:2px solid {C_MOD};
                        border-radius:0 0 6px 6px;padding:14px 20px;background:{C_CARD}">
                <div style="font-family:'Libre Franklin',sans-serif;font-size:0.64rem;font-weight:700;
                            color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.06em">Avg Volatility</div>
                <div style="font-family:JetBrains Mono,monospace;font-size:1.2rem;
                            font-weight:700;color:{C_TEXT};margin-top:2px">{regime_data.get('avg_volatility',0):.1f}%</div>
            </div>
        </div>
    </div>
    """, )

    # ── 2. Route-Level Regime Table ──────────────────────────────────────────
    routes = regime_data.get("routes", {})
    if routes:
        st.html(f"""
        <div style="border-top:2px solid {C_TEXT};padding-top:10px;margin-bottom:14px">
            <div style="font-family:'Libre Baskerville',Georgia,serif;font-size:1rem;
                        font-weight:700;color:{C_TEXT}">Route-Level Rate Regimes</div>
        </div>
        """)

        headers = ["Route", "Current", "Mean", "Z-Score", "Pctile", "Regime", "30d Trend", "Volatility"]
        hdr = "<tr>"
        for h in headers:
            align = "left" if h == "Route" else "right"
            hdr += f'<th style="text-align:{align};font-family:Libre Franklin,sans-serif;font-size:0.62rem;font-weight:700;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.06em;padding:6px 10px;border-bottom:2px solid {C_TEXT}">{h}</th>'
        hdr += "</tr>"

        tbody = ""
        sorted_routes = sorted(routes.items(), key=lambda x: x[1]["z_score"], reverse=True)
        for route_key, r in sorted_routes:
            rc = _regime_color(r["regime"])
            z_color = C_HIGH if r["z_score"] > 0.5 else (C_LOW if r["z_score"] < -0.5 else C_TEXT2)
            trend_color = C_HIGH if r["trend_30d_pct"] > 0 else (C_LOW if r["trend_30d_pct"] < 0 else C_TEXT2)
            sign = "+" if r["trend_30d_pct"] >= 0 else ""

            # Percentile bar
            pct = r["percentile"]
            pct_color = C_HIGH if pct > 70 else (C_LOW if pct < 30 else C_TEXT2)

            tbody += f"""
            <tr>
                <td style="font-family:'Libre Baskerville',Georgia,serif;font-size:0.8rem;font-weight:700;
                           color:{C_TEXT};padding:8px 10px;border-bottom:1px solid {C_RULE};max-width:160px;
                           overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{str(route_key)[:20]}</td>
                <td style="text-align:right;font-family:JetBrains Mono,monospace;font-size:0.8rem;
                           font-weight:600;color:{C_TEXT};padding:8px 10px;border-bottom:1px solid {C_RULE}">{r['current']:,.0f}</td>
                <td style="text-align:right;font-family:JetBrains Mono,monospace;font-size:0.8rem;
                           color:{C_TEXT2};padding:8px 10px;border-bottom:1px solid {C_RULE}">{r['mean']:,.0f}</td>
                <td style="text-align:right;font-family:JetBrains Mono,monospace;font-size:0.8rem;
                           font-weight:600;color:{z_color};padding:8px 10px;border-bottom:1px solid {C_RULE}">{r['z_score']:+.2f}</td>
                <td style="text-align:right;font-family:JetBrains Mono,monospace;font-size:0.8rem;
                           color:{pct_color};padding:8px 10px;border-bottom:1px solid {C_RULE}">{pct:.0f}th</td>
                <td style="text-align:right;padding:8px 10px;border-bottom:1px solid {C_RULE}">
                    <span style="background:{_rgba(rc,0.08)};color:{rc};border:1px solid {_rgba(rc,0.2)};
                                 padding:2px 8px;border-radius:3px;font-size:0.66rem;font-weight:700;
                                 font-family:Libre Franklin,sans-serif">{r['regime']}</span></td>
                <td style="text-align:right;font-family:JetBrains Mono,monospace;font-size:0.8rem;
                           font-weight:600;color:{trend_color};padding:8px 10px;
                           border-bottom:1px solid {C_RULE}">{sign}{r['trend_30d_pct']:.1f}%</td>
                <td style="text-align:right;font-family:JetBrains Mono,monospace;font-size:0.8rem;
                           color:{C_TEXT2};padding:8px 10px;border-bottom:1px solid {C_RULE}">{r['volatility_annual']:.1f}%</td>
            </tr>
            """

        st.html(f"""
        <div style="overflow-x:auto">
        <table style="width:100%;border-collapse:collapse;min-width:700px">
            <thead>{hdr}</thead>
            <tbody>{tbody}</tbody>
        </table>
        </div>
        """)

    # ── 3. Rate Spreads ──────────────────────────────────────────────────────
    spreads = compute_rate_spreads(freight_data)
    if spreads:
        st.html(f"""
        <div style="border-top:2px solid {C_TEXT};padding-top:10px;margin-bottom:14px;margin-top:28px">
            <div style="font-family:'Libre Baskerville',Georgia,serif;font-size:1rem;
                        font-weight:700;color:{C_TEXT}">Rate Spread Analysis</div>
            <div style="font-family:'Libre Franklin',sans-serif;font-size:0.72rem;
                        color:{C_TEXT3};margin-top:2px">Most dislocated route pairs by z-score</div>
        </div>
        """)

        shdr = "<tr>"
        for h in ["Route Pair", "Spread", "Mean", "Z-Score", "Correlation", "Signal"]:
            align = "left" if h == "Route Pair" else "right"
            shdr += f'<th style="text-align:{align};font-family:Libre Franklin,sans-serif;font-size:0.62rem;font-weight:700;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.06em;padding:6px 10px;border-bottom:2px solid {C_TEXT}">{h}</th>'
        shdr += "</tr>"

        stbody = ""
        for sp in spreads[:8]:
            z_color = C_HIGH if sp["z_score"] > 1 else (C_LOW if sp["z_score"] < -1 else C_TEXT2)
            sig_color = C_LOW if sp["signal"] == "Wide" else (C_HIGH if sp["signal"] == "Narrow" else C_TEXT2)

            stbody += f"""
            <tr>
                <td style="font-family:Libre Franklin,sans-serif;font-size:0.78rem;font-weight:600;
                           color:{C_TEXT};padding:8px 10px;border-bottom:1px solid {C_RULE}">
                    {str(sp['route1'])[:14]} / {str(sp['route2'])[:14]}</td>
                <td style="text-align:right;font-family:JetBrains Mono,monospace;font-size:0.8rem;
                           color:{C_TEXT};padding:8px 10px;border-bottom:1px solid {C_RULE}">{sp['current_spread']:+,.0f}</td>
                <td style="text-align:right;font-family:JetBrains Mono,monospace;font-size:0.8rem;
                           color:{C_TEXT2};padding:8px 10px;border-bottom:1px solid {C_RULE}">{sp['mean_spread']:+,.0f}</td>
                <td style="text-align:right;font-family:JetBrains Mono,monospace;font-size:0.8rem;
                           font-weight:600;color:{z_color};padding:8px 10px;border-bottom:1px solid {C_RULE}">{sp['z_score']:+.2f}</td>
                <td style="text-align:right;font-family:JetBrains Mono,monospace;font-size:0.8rem;
                           color:{C_TEXT2};padding:8px 10px;border-bottom:1px solid {C_RULE}">{sp['correlation']:.2f}</td>
                <td style="text-align:right;padding:8px 10px;border-bottom:1px solid {C_RULE}">
                    <span style="background:{_rgba(sig_color,0.08)};color:{sig_color};
                                 border:1px solid {_rgba(sig_color,0.2)};
                                 padding:2px 8px;border-radius:3px;font-size:0.66rem;
                                 font-weight:700;font-family:Libre Franklin,sans-serif">{sp['signal']}</span></td>
            </tr>
            """

        st.html(f"""
        <table style="width:100%;border-collapse:collapse">
            <thead>{shdr}</thead>
            <tbody>{stbody}</tbody>
        </table>
        """)
