"""tab_commentary.py — WSJ-style Daily Market Commentary page.

Displays auto-generated editorial narrative about market conditions,
key movers, forward outlook, and index dashboard.
"""
from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st
from loguru import logger

C_BG    = "#0c0e14"
C_CARD  = "#181c28"
C_RULE  = "rgba(232,230,225,0.12)"
C_HIGH  = "#2e9e6e"
C_MOD   = "#c9962b"
C_LOW   = "#c0392b"
C_ACCENT = "#3572b0"
C_TEXT  = "#e8e6e1"
C_TEXT2 = "#9a968e"
C_TEXT3 = "#6b6760"


def _rgba(h: str, a: float) -> str:
    try:
        h2 = h.lstrip("#")
        r, g, b = int(h2[0:2], 16), int(h2[2:4], 16), int(h2[4:6], 16)
        return f"rgba({r},{g},{b},{a})"
    except Exception:
        return f"rgba(255,255,255,{a})"


def _tone_color(tone: str) -> str:
    if tone == "BULLISH": return C_HIGH
    if tone == "BEARISH": return C_LOW
    return C_MOD


def render(stock_data=None, freight_data=None, macro_data=None,
           port_results=None, insights=None, **kwargs) -> None:
    """Render the daily market commentary dashboard."""
    try:
        from processing.market_commentary import generate_daily_wrap, generate_forward_outlook
        from processing.index_tracker import compute_index_dashboard
    except ImportError as e:
        st.error(f"Commentary modules unavailable: {e}")
        return

    stock_data = stock_data or {}
    freight_data = freight_data or {}
    macro_data = macro_data or {}
    port_results = port_results or []
    insights = insights or []

    # Generate data
    wrap = generate_daily_wrap(stock_data, freight_data, macro_data, port_results, insights)
    outlook = generate_forward_outlook(insights, macro_data, freight_data)
    indices = compute_index_dashboard(freight_data, macro_data)

    tc = _tone_color(wrap["market_tone"])

    # ── 1. WSJ Editorial Header ──────────────────────────────────────────────
    st.html(f"""
    <div style="margin-bottom:24px">
        <div style="border-top:2px solid {C_TEXT};padding-top:12px;margin-bottom:6px">
            <div style="display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px">
                <div style="font-family:'Libre Franklin',sans-serif;font-size:0.62rem;font-weight:700;
                            color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.12em">
                    Daily Market Commentary</div>
                <div style="display:flex;align-items:center;gap:8px">
                    <span style="font-family:'Libre Franklin',sans-serif;font-size:0.68rem;
                                 color:{C_TEXT3}">{wrap['date']}</span>
                    <span style="background:{_rgba(tc,0.08)};color:{tc};border:1px solid {_rgba(tc,0.2)};
                                 padding:2px 8px;border-radius:3px;font-size:0.66rem;font-weight:700;
                                 font-family:'Libre Franklin',sans-serif">{wrap['market_tone']}</span>
                </div>
            </div>
        </div>

        <!-- Main headline -->
        <div style="font-family:'Libre Baskerville',Georgia,serif;font-size:1.6rem;font-weight:700;
                    color:{C_TEXT};line-height:1.25;letter-spacing:-0.02em;margin-bottom:8px">
            {wrap['headline']}</div>

        <!-- Subhead -->
        <div style="font-family:'Libre Franklin',sans-serif;font-size:0.92rem;color:{C_TEXT2};
                    line-height:1.5;margin-bottom:16px;border-bottom:1px solid {C_RULE};padding-bottom:14px">
            {wrap['subhead']}</div>

        <!-- Body paragraphs -->
        {''.join(f'<p style="font-family:Libre Franklin,sans-serif;font-size:0.88rem;color:{C_TEXT2};line-height:1.7;margin-bottom:12px">{p}</p>' for p in wrap.get('body', []))}
    </div>
    """)

    # ── 2. Key Movers ────────────────────────────────────────────────────────
    movers = wrap.get("key_movers", [])
    if movers:
        st.html(f"""
        <div style="border-top:2px solid {C_TEXT};padding-top:10px;margin-bottom:14px">
            <div style="font-family:'Libre Baskerville',Georgia,serif;font-size:1rem;
                        font-weight:700;color:{C_TEXT}">Key Movers</div>
        </div>
        """)

        cols = st.columns(min(len(movers), 5))
        for col, m in zip(cols, movers):
            with col:
                chg = m["change_pct"]
                c = C_HIGH if chg > 0 else C_LOW
                sign = "+" if chg >= 0 else ""
                st.html(f"""
                <div style="border:1px solid {C_RULE};border-top:2px solid {c};
                            border-radius:0 0 6px 6px;padding:12px 14px;background:{C_CARD};text-align:center">
                    <div style="font-family:'Libre Franklin',sans-serif;font-size:0.78rem;
                                font-weight:700;color:{C_TEXT}">{m['ticker']}</div>
                    <div style="font-family:JetBrains Mono,monospace;font-size:1.1rem;
                                font-weight:700;color:{C_TEXT};margin:4px 0">${m['price']:.2f}</div>
                    <div style="font-family:JetBrains Mono,monospace;font-size:0.82rem;
                                font-weight:600;color:{c}">{sign}{chg:.1f}%</div>
                </div>
                """)

    # ── 3. Shipping Indices ──────────────────────────────────────────────────
    if indices:
        st.html(f"""
        <div style="border-top:2px solid {C_TEXT};padding-top:10px;margin-bottom:14px;margin-top:24px">
            <div style="font-family:'Libre Baskerville',Georgia,serif;font-size:1rem;
                        font-weight:700;color:{C_TEXT}">Shipping Indices</div>
            <div style="font-family:'Libre Franklin',sans-serif;font-size:0.72rem;
                        color:{C_TEXT3};margin-top:2px">Key freight benchmarks with historical context</div>
        </div>
        """)

        for idx in indices:
            current = idx.get("current")
            if current is None:
                continue

            chg_1d = idx.get("change_1d")
            chg_color = C_HIGH if chg_1d and chg_1d > 0 else (C_LOW if chg_1d and chg_1d < 0 else C_TEXT2)
            chg_str = f'+{chg_1d:.1f}%' if chg_1d and chg_1d >= 0 else (f'{chg_1d:.1f}%' if chg_1d else '--')

            pctile = idx.get("percentile", 50)
            range_pos = idx.get("range_position", 50)
            high_52 = idx.get("high_52w")
            low_52 = idx.get("low_52w")
            momentum = idx.get("momentum", "N/A")
            mom_color = C_HIGH if "Rally" in momentum or "Uptrend" in momentum else (
                C_LOW if "Decline" in momentum or "Downtrend" in momentum else C_TEXT2)

            commentary = idx.get("commentary", "")

            st.html(f"""
            <div style="background:{C_CARD};border:1px solid {C_RULE};border-radius:6px;
                        padding:16px 20px;margin-bottom:12px">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:12px">
                    <div style="flex:1;min-width:240px">
                        <div style="display:flex;align-items:baseline;gap:10px;margin-bottom:4px">
                            <span style="font-family:'Libre Baskerville',Georgia,serif;font-size:1rem;
                                         font-weight:700;color:{C_TEXT}">{idx['name']}</span>
                            <span style="font-family:'Libre Franklin',sans-serif;font-size:0.66rem;
                                         color:{C_TEXT3}">{idx['source']}</span>
                        </div>
                        <div style="font-family:'Libre Franklin',sans-serif;font-size:0.82rem;
                                    color:{C_TEXT2};line-height:1.5;margin-top:6px">{commentary}</div>
                    </div>
                    <div style="display:flex;gap:16px;align-items:flex-start;flex-shrink:0">
                        <div style="text-align:right">
                            <div style="font-family:JetBrains Mono,monospace;font-size:1.4rem;
                                        font-weight:700;color:{C_TEXT}">{current:,.0f}</div>
                            <div style="font-family:JetBrains Mono,monospace;font-size:0.82rem;
                                        font-weight:600;color:{chg_color}">{chg_str}</div>
                        </div>
                        <div style="text-align:center">
                            <div style="font-family:JetBrains Mono,monospace;font-size:0.82rem;
                                        font-weight:600;color:{C_TEXT}">{pctile:.0f}th</div>
                            <div style="font-family:'Libre Franklin',sans-serif;font-size:0.6rem;
                                        color:{C_TEXT3};text-transform:uppercase">Percentile</div>
                        </div>
                        <div>
                            <span style="background:{_rgba(mom_color,0.08)};color:{mom_color};
                                         border:1px solid {_rgba(mom_color,0.2)};
                                         padding:3px 8px;border-radius:3px;font-size:0.66rem;
                                         font-weight:700;font-family:'Libre Franklin',sans-serif">{momentum}</span>
                        </div>
                    </div>
                </div>
                <!-- 52-week range bar -->
                {'<div style="margin-top:10px"><div style="display:flex;justify-content:space-between;margin-bottom:3px"><span style="font-family:JetBrains Mono,monospace;font-size:0.68rem;color:' + C_TEXT3 + '">' + f'{low_52:,.0f}' + '</span><span style="font-family:Libre Franklin,sans-serif;font-size:0.6rem;color:' + C_TEXT3 + ';text-transform:uppercase">52-Week Range</span><span style="font-family:JetBrains Mono,monospace;font-size:0.68rem;color:' + C_TEXT3 + '">' + f'{high_52:,.0f}' + '</span></div><div style="height:4px;background:' + _rgba(C_TEXT, 0.06) + ';border-radius:2px;position:relative"><div style="position:absolute;left:' + f'{range_pos:.0f}' + '%;top:-2px;width:8px;height:8px;border-radius:50%;background:' + C_ACCENT + ';transform:translateX(-50%)"></div></div></div>' if high_52 and low_52 else ''}
            </div>
            """)

    # ── 4. Forward Outlook ───────────────────────────────────────────────────
    st.html(f"""
    <div style="border-top:2px solid {C_TEXT};padding-top:10px;margin-bottom:14px;margin-top:24px">
        <div style="font-family:'Libre Baskerville',Georgia,serif;font-size:1rem;
                    font-weight:700;color:{C_TEXT}">Forward Outlook</div>
    </div>
    """)

    # Narrative
    st.html(f"""
    <div style="font-family:'Libre Franklin',sans-serif;font-size:0.88rem;color:{C_TEXT2};
                line-height:1.65;margin-bottom:16px">{outlook.get('narrative', '')}</div>
    """)

    c1, c2 = st.columns(2)
    with c1:
        opps = outlook.get("opportunities", [])
        if opps:
            st.html(f"""
            <div style="border-top:2px solid {C_HIGH};padding-top:8px">
                <div style="font-family:'Libre Franklin',sans-serif;font-size:0.68rem;font-weight:700;
                            color:{C_HIGH};text-transform:uppercase;letter-spacing:0.06em;margin-bottom:8px">
                    Opportunities</div>
            """)
            for o in opps:
                st.html(f"""
                <div style="padding:6px 0;border-bottom:1px dotted {_rgba(C_TEXT,0.04)}">
                    <div style="font-family:'Libre Baskerville',Georgia,serif;font-size:0.82rem;
                                font-weight:700;color:{C_TEXT};line-height:1.3">{o['title'][:80]}</div>
                    <div style="font-family:JetBrains Mono,monospace;font-size:0.7rem;
                                color:{C_HIGH};margin-top:2px">{o['score']:.0%} conviction</div>
                </div>
                """)
            st.html("</div>")

    with c2:
        risks = outlook.get("risks", [])
        if risks:
            st.html(f"""
            <div style="border-top:2px solid {C_LOW};padding-top:8px">
                <div style="font-family:'Libre Franklin',sans-serif;font-size:0.68rem;font-weight:700;
                            color:{C_LOW};text-transform:uppercase;letter-spacing:0.06em;margin-bottom:8px">
                    Key Risks</div>
            """)
            for r in risks:
                st.html(f"""
                <div style="padding:6px 0;border-bottom:1px dotted {_rgba(C_TEXT,0.04)}">
                    <div style="font-family:'Libre Baskerville',Georgia,serif;font-size:0.82rem;
                                font-weight:700;color:{C_TEXT};line-height:1.3">{r['title'][:80]}</div>
                    <div style="font-family:JetBrains Mono,monospace;font-size:0.7rem;
                                color:{C_LOW};margin-top:2px">{r.get('category','').lower().replace('_',' ')}</div>
                </div>
                """)
            st.html("</div>")
