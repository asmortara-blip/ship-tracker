"""tab_sector.py — WSJ-style Shipping Sector Comparative Dashboard.

Displays:
  1. Sector performance comparison table
  2. Relative performance chart
  3. Freight index summary
  4. Global trade flow regional breakdown
  5. Sector outlook and momentum signals
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


def _chg_color(val: float | None) -> str:
    if val is None: return C_TEXT3
    if val > 0: return C_HIGH
    if val < 0: return C_LOW
    return C_TEXT2


def _fmt_pct(val: float | None) -> str:
    if val is None: return "--"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.1f}%"


def _outlook_color(outlook: str) -> str:
    if "Bullish" in outlook: return C_HIGH
    if "Positive" in outlook: return "#4a8a6a"
    if "Negative" in outlook: return C_MOD
    if "Bearish" in outlook: return C_LOW
    return C_TEXT3


def render(stock_data=None, freight_data=None, trade_data=None, port_results=None, **kwargs) -> None:
    """Render the Shipping Sector Dashboard."""
    try:
        from processing.sector_dashboard import compute_sector_performance, compute_trade_flow_summary
    except ImportError as e:
        st.error(f"Sector dashboard module not available: {e}")
        return

    stock_data = stock_data or {}
    freight_data = freight_data or {}
    port_results = port_results or []

    # ── 1. Sector Performance Comparison ─────────────────────────────────────
    sectors = compute_sector_performance(stock_data, freight_data)

    st.html(f"""
    <div style="margin-bottom:24px">
        <div style="border-top:2px solid {C_TEXT};padding-top:10px;margin-bottom:16px">
            <div style="font-family:'Libre Baskerville',Georgia,serif;font-size:1.35rem;
                        font-weight:700;color:{C_TEXT}">Shipping Sector Dashboard</div>
            <div style="font-family:'Libre Franklin',sans-serif;font-size:0.78rem;
                        color:{C_TEXT2};margin-top:4px;line-height:1.5">
                Comparative performance across container, dry bulk, tanker, and LNG segments.
                Data reflects latest available market prices and freight indices.</div>
        </div>
    </div>
    """)

    # Sector comparison table
    st.html(f"""
    <div style="border-top:2px solid {C_TEXT};padding-top:10px;margin-bottom:14px;margin-top:8px">
        <div style="font-family:'Libre Baskerville',Georgia,serif;font-size:1rem;
                    font-weight:700;color:{C_TEXT}">Sector Performance</div>
    </div>
    """)

    headers = ["Sector", "1-Day", "5-Day", "30-Day", "Index", "Index Chg", "Momentum", "Outlook"]
    hdr_html = "<tr>"
    for h in headers:
        align = "left" if h == "Sector" else "right"
        hdr_html += f'<th style="text-align:{align};font-family:Libre Franklin,sans-serif;font-size:0.64rem;font-weight:700;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.08em;padding:6px 12px;border-bottom:2px solid {C_TEXT}">{h}</th>'
    hdr_html += "</tr>"

    tbody = ""
    for s in sectors:
        ret1 = s.get("avg_return_1d")
        ret5 = s.get("avg_return_5d")
        ret30 = s.get("avg_return_30d")
        idx_val = s.get("index_current")
        idx_chg = s.get("index_chg_1d")
        momentum = s.get("momentum", "N/A")
        outlook = s.get("outlook", "N/A")
        olc = _outlook_color(outlook)

        mom_color = C_HIGH if momentum == "Strong" else (C_MOD if momentum == "Positive" else (C_LOW if momentum == "Negative" else C_TEXT3))

        idx_str = f'{idx_val:,.0f}' if idx_val else "--"

        tbody += f"""
        <tr>
            <td style="font-family:'Libre Baskerville',Georgia,serif;font-size:0.84rem;font-weight:700;
                       color:{C_TEXT};padding:10px 12px;border-bottom:1px solid {C_RULE}">
                {s['name']}
                <div style="font-family:Libre Franklin,sans-serif;font-size:0.68rem;color:{C_TEXT3};
                            font-weight:400;margin-top:2px">{s.get('description','')[:50]}</div>
            </td>
            <td style="text-align:right;font-family:JetBrains Mono,monospace;font-size:0.82rem;
                       font-weight:600;color:{_chg_color(ret1)};padding:10px 12px;
                       border-bottom:1px solid {C_RULE}">{_fmt_pct(ret1)}</td>
            <td style="text-align:right;font-family:JetBrains Mono,monospace;font-size:0.82rem;
                       font-weight:600;color:{_chg_color(ret5)};padding:10px 12px;
                       border-bottom:1px solid {C_RULE}">{_fmt_pct(ret5)}</td>
            <td style="text-align:right;font-family:JetBrains Mono,monospace;font-size:0.82rem;
                       font-weight:600;color:{_chg_color(ret30)};padding:10px 12px;
                       border-bottom:1px solid {C_RULE}">{_fmt_pct(ret30)}</td>
            <td style="text-align:right;font-family:JetBrains Mono,monospace;font-size:0.82rem;
                       color:{C_TEXT};padding:10px 12px;border-bottom:1px solid {C_RULE}">{idx_str}</td>
            <td style="text-align:right;font-family:JetBrains Mono,monospace;font-size:0.82rem;
                       font-weight:600;color:{_chg_color(idx_chg)};padding:10px 12px;
                       border-bottom:1px solid {C_RULE}">{_fmt_pct(idx_chg)}</td>
            <td style="text-align:right;font-family:Libre Franklin,sans-serif;font-size:0.78rem;
                       font-weight:600;color:{mom_color};padding:10px 12px;
                       border-bottom:1px solid {C_RULE}">{momentum}</td>
            <td style="text-align:right;padding:10px 12px;border-bottom:1px solid {C_RULE}">
                <span style="background:{_rgba(olc,0.08)};color:{olc};
                             border:1px solid {_rgba(olc,0.2)};
                             padding:2px 8px;border-radius:3px;font-size:0.68rem;
                             font-weight:700;font-family:Libre Franklin,sans-serif">{outlook}</span>
            </td>
        </tr>
        """

    st.html(f"""
    <table style="width:100%;border-collapse:collapse">
        <thead>{hdr_html}</thead>
        <tbody>{tbody}</tbody>
    </table>
    """)

    # ── 2. Sector Detail Cards ───────────────────────────────────────────────
    st.html(f"""
    <div style="border-top:2px solid {C_TEXT};padding-top:10px;margin-bottom:14px;margin-top:28px">
        <div style="font-family:'Libre Baskerville',Georgia,serif;font-size:1rem;
                    font-weight:700;color:{C_TEXT}">Sector Profiles</div>
        <div style="font-family:'Libre Franklin',sans-serif;font-size:0.72rem;
                    color:{C_TEXT3};margin-top:2px">Key drivers and constituent equities</div>
    </div>
    """)

    cols = st.columns(len(sectors))
    for col, s in zip(cols, sectors):
        with col:
            olc = _outlook_color(s.get("outlook", "N/A"))
            ret30 = s.get("avg_return_30d")
            ret30_color = _chg_color(ret30)

            # Stock prices
            price_rows = ""
            for p in s.get("stock_prices", []):
                price_rows += f"""
                <div style="display:flex;justify-content:space-between;padding:3px 0;
                            border-bottom:1px dotted {_rgba(C_TEXT,0.04)}">
                    <span style="font-family:Libre Franklin,sans-serif;font-size:0.76rem;
                                 font-weight:600;color:{C_TEXT}">{p['ticker']}</span>
                    <span style="font-family:JetBrains Mono,monospace;font-size:0.76rem;
                                 color:{C_TEXT2}">${p['price']:.2f}</span>
                </div>
                """
            if not price_rows:
                price_rows = f'<div style="font-size:0.76rem;color:{C_TEXT3};padding:4px 0">No tracked equities</div>'

            st.html(f"""
            <div style="border:1px solid {C_RULE};border-top:2px solid {olc};
                        border-radius:0 0 6px 6px;padding:16px;background:{C_CARD}">
                <div style="font-family:'Libre Baskerville',Georgia,serif;font-size:0.95rem;
                            font-weight:700;color:{C_TEXT};margin-bottom:4px">{s['name']}</div>
                <div style="font-family:Libre Franklin,sans-serif;font-size:0.72rem;
                            color:{C_TEXT3};margin-bottom:10px;line-height:1.4">{s.get('key_driver','')}</div>

                <div style="display:flex;justify-content:space-between;align-items:baseline;
                            margin-bottom:8px;padding-bottom:6px;border-bottom:1px solid {C_RULE}">
                    <span style="font-family:Libre Franklin,sans-serif;font-size:0.64rem;
                                 font-weight:700;color:{C_TEXT3};text-transform:uppercase;
                                 letter-spacing:0.06em">30d Return</span>
                    <span style="font-family:JetBrains Mono,monospace;font-size:1.1rem;
                                 font-weight:700;color:{ret30_color}">{_fmt_pct(ret30)}</span>
                </div>

                {price_rows}

                <div style="margin-top:8px;text-align:center">
                    <span style="background:{_rgba(olc,0.08)};color:{olc};
                                 border:1px solid {_rgba(olc,0.2)};
                                 padding:3px 12px;border-radius:3px;font-size:0.68rem;
                                 font-weight:700;font-family:Libre Franklin,sans-serif">
                        {s.get('outlook','N/A')}</span>
                </div>
            </div>
            """)

    # ── 3. Global Trade Flow Summary ─────────────────────────────────────────
    if port_results:
        trade_summary = compute_trade_flow_summary(trade_data or {}, port_results)

        st.html(f"""
        <div style="border-top:2px solid {C_TEXT};padding-top:10px;margin-bottom:14px;margin-top:28px">
            <div style="font-family:'Libre Baskerville',Georgia,serif;font-size:1rem;
                        font-weight:700;color:{C_TEXT}">Global Trade Flows</div>
            <div style="font-family:'Libre Franklin',sans-serif;font-size:0.72rem;
                        color:{C_TEXT3};margin-top:2px">Regional breakdown of tracked trade volumes</div>
        </div>
        """)

        # Narrative
        narrative = trade_summary.get("narrative", "")
        if narrative:
            st.html(f"""
            <div style="font-family:'Libre Franklin',sans-serif;font-size:0.88rem;
                        color:{C_TEXT2};line-height:1.65;margin-bottom:16px;max-width:720px">
                {narrative}</div>
            """)

        # Regional table
        regions = trade_summary.get("regions", {})
        active_regions = [(k, v) for k, v in regions.items() if v.get("ports")]
        active_regions.sort(key=lambda x: x[1].get("total_trade", 0), reverse=True)

        if active_regions:
            rhdr = "<tr>"
            for h in ["Region", "Ports", "Trade Volume", "Share"]:
                align = "left" if h == "Region" else "right"
                rhdr += f'<th style="text-align:{align};font-family:Libre Franklin,sans-serif;font-size:0.64rem;font-weight:700;color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.08em;padding:6px 12px;border-bottom:2px solid {C_TEXT}">{h}</th>'
            rhdr += "</tr>"

            rtbody = ""
            for region_name, region_data in active_regions:
                trade_val = region_data.get("total_trade", 0)
                share = region_data.get("share_pct", 0)
                n_ports = len(region_data.get("ports", []))

                if trade_val >= 1e9:
                    trade_str = f"${trade_val/1e9:.1f}B"
                elif trade_val >= 1e6:
                    trade_str = f"${trade_val/1e6:.0f}M"
                elif trade_val > 0:
                    trade_str = f"${trade_val:,.0f}"
                else:
                    trade_str = "--"

                # Share bar
                bar_width = min(share, 100)
                bar_html = f"""
                <div style="display:flex;align-items:center;gap:6px;justify-content:flex-end">
                    <div style="width:60px;height:3px;background:{_rgba(C_TEXT,0.06)};border-radius:2px;overflow:hidden">
                        <div style="height:100%;width:{bar_width}%;background:{_rgba(C_ACCENT,0.5)};border-radius:2px"></div>
                    </div>
                    <span style="font-family:JetBrains Mono,monospace;font-size:0.78rem;
                                 color:{C_TEXT2};min-width:36px;text-align:right">{share:.0f}%</span>
                </div>
                """

                rtbody += f"""
                <tr>
                    <td style="font-family:'Libre Baskerville',Georgia,serif;font-size:0.84rem;font-weight:700;
                               color:{C_TEXT};padding:8px 12px;border-bottom:1px solid {C_RULE}">{region_name}</td>
                    <td style="text-align:right;font-family:JetBrains Mono,monospace;font-size:0.82rem;
                               color:{C_TEXT2};padding:8px 12px;border-bottom:1px solid {C_RULE}">{n_ports}</td>
                    <td style="text-align:right;font-family:JetBrains Mono,monospace;font-size:0.82rem;
                               color:{C_TEXT};padding:8px 12px;border-bottom:1px solid {C_RULE}">{trade_str}</td>
                    <td style="text-align:right;padding:8px 12px;border-bottom:1px solid {C_RULE}">{bar_html}</td>
                </tr>
                """

            st.html(f"""
            <table style="width:100%;border-collapse:collapse">
                <thead>{rhdr}</thead>
                <tbody>{rtbody}</tbody>
            </table>
            """)
