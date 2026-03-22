"""
Options Screener Tab — Derivatives Flow & Volatility Intelligence

Sections:
  1. Hero header
  2. Filter controls
  3. Unusual activity cards
  4. Full options chain table
  5. IV surface heatmap
  6. Max pain chart
  7. Put/call ratio gauge and history
  8. Strategy screener
"""
from __future__ import annotations

import streamlit as st
import plotly.graph_objects as go

from ui.styles import (
    C_ACCENT, C_BORDER, C_CARD, C_HIGH, C_LOW, C_MOD,
    C_TEXT, C_TEXT2, C_TEXT3, dark_layout,
)

# ── Palette ────────────────────────────────────────────────────────────────────
C_BG      = "#0a0f1a"
C_SURFACE = "#111827"

_ALL_TICKERS = ["ZIM", "MATX", "DAC", "SBLK", "STNG", "GSL"]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _mid(opt) -> float:
    return round((opt.bid + opt.ask) / 2.0, 2)


def _vol_oi_ratio(opt) -> float:
    return round(opt.volume / opt.oi, 2) if opt.oi > 0 else 0.0


def _iv_pct(iv: float) -> str:
    return f"{iv * 100:.1f}%"


def _delta_color(delta: float, call_put: str) -> str:
    if call_put == "C":
        return C_HIGH if delta >= 0.4 else (C_MOD if delta >= 0.2 else C_TEXT3)
    else:
        return C_LOW if delta <= -0.4 else (C_MOD if delta <= -0.2 else C_TEXT3)


def _strategy_ideas(options: list) -> list[dict]:
    """
    Identify simple strategy opportunities based on IV levels and moneyness.
    Returns list of dicts with: type, ticker, expiry, strike, iv_pct, rationale, color.
    """
    ideas: list[dict] = []
    seen: set[str] = set()

    for opt in options:
        key = f"{opt.ticker}-{opt.expiry}-{opt.strike}-{opt.call_put}"
        if key in seen:
            continue
        seen.add(key)

        iv_pct = opt.iv * 100

        # Covered call: call with IV > 60%, delta 0.25–0.45, near ATM
        if (opt.call_put == "C"
                and iv_pct > 60
                and 0.25 <= opt.delta <= 0.45
                and 0.97 <= opt.moneyness <= 1.10):
            ideas.append({
                "type": "Covered Call",
                "ticker": opt.ticker,
                "expiry": opt.expiry,
                "strike": opt.strike,
                "iv_pct": iv_pct,
                "rationale": f"IV {iv_pct:.0f}% — elevated premium, delta {opt.delta:.2f}",
                "color": C_HIGH,
                "icon": "📈",
            })

        # Protective put: put with IV < 70%, delta < -0.30, moderate OI
        elif (opt.call_put == "P"
              and iv_pct < 70
              and opt.delta <= -0.30
              and opt.oi >= 300):
            ideas.append({
                "type": "Protective Put",
                "ticker": opt.ticker,
                "expiry": opt.expiry,
                "strike": opt.strike,
                "iv_pct": iv_pct,
                "rationale": f"Cheap downside hedge — IV {iv_pct:.0f}%, delta {opt.delta:.2f}",
                "color": C_MOD,
                "icon": "🛡️",
            })

        # Straddle candidate: near ATM with high IV
        elif (0.98 <= opt.moneyness <= 1.02
              and iv_pct > 75
              and opt.oi >= 200):
            ideas.append({
                "type": "Straddle",
                "ticker": opt.ticker,
                "expiry": opt.expiry,
                "strike": opt.strike,
                "iv_pct": iv_pct,
                "rationale": f"High IV {iv_pct:.0f}% at ATM — volatility expansion play",
                "color": C_ACCENT,
                "icon": "↕️",
            })

        if len(ideas) >= 12:
            break

    return ideas


# ── Main render ────────────────────────────────────────────────────────────────

def render(stock_data, insights):
    try:
        from processing.options_screener import (
            screen_options,
            get_iv_surface,
            get_unusual_activity,
            calculate_max_pain,
        )
    except Exception as e:
        st.error(f"Options screener module unavailable: {e}")
        return

    # ── 1. Hero header ─────────────────────────────────────────────────────────
    st.markdown(
        f'<div style="background:linear-gradient(135deg,{C_SURFACE} 0%,{C_CARD} 100%);'
        f'border:1px solid {C_BORDER};border-radius:12px;padding:28px 32px;margin-bottom:24px;">'
        f'<div style="display:flex;align-items:center;gap:14px;margin-bottom:6px;">'
        f'<span style="font-size:2rem;">📊</span>'
        f'<h1 style="margin:0;font-size:1.9rem;font-weight:800;'
        f'background:linear-gradient(90deg,{C_TEXT},{C_ACCENT});'
        f'-webkit-background-clip:text;-webkit-text-fill-color:transparent;">'
        f'Options Screener</h1></div>'
        f'<p style="margin:0;color:{C_TEXT2};font-size:1rem;letter-spacing:0.03em;">'
        f'Derivatives Flow &amp; Volatility Intelligence &nbsp;·&nbsp; '
        f'Shipping Equity Options &nbsp;·&nbsp; Mock Data</p></div>',
        unsafe_allow_html=True,
    )

    # ── 2. Filter controls ─────────────────────────────────────────────────────
    f1, f2, f3, f4 = st.columns([2, 1.2, 1, 1.2])

    with f1:
        selected_tickers = st.multiselect(
            "Tickers",
            options=_ALL_TICKERS,
            default=_ALL_TICKERS,
            key="opt_tickers",
        )

    with f2:
        min_oi = st.slider(
            "Min Open Interest",
            min_value=0, max_value=2000,
            value=100, step=50,
            key="opt_min_oi",
        )

    with f3:
        cp_filter = st.selectbox(
            "Call / Put",
            options=["Both", "Calls Only", "Puts Only"],
            key="opt_cp",
        )

    with f4:
        moneyness_filter = st.selectbox(
            "Moneyness",
            options=["All", "ATM (±3%)", "OTM only", "ITM only"],
            key="opt_moneyness",
        )

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    # ── Load data ──────────────────────────────────────────────────────────────
    try:
        tickers_to_use = selected_tickers if selected_tickers else _ALL_TICKERS
        all_options = screen_options(tickers_to_use, min_oi=min_oi, max_iv=2.0)
    except Exception as e:
        st.error(f"Failed to generate options data: {e}")
        return

    if not all_options:
        st.warning("No options matched your filters.")
        return

    # Apply call/put filter
    if cp_filter == "Calls Only":
        all_options = [o for o in all_options if o.call_put == "C"]
    elif cp_filter == "Puts Only":
        all_options = [o for o in all_options if o.call_put == "P"]

    # Apply moneyness filter
    if moneyness_filter == "ATM (±3%)":
        all_options = [o for o in all_options if 0.97 <= o.moneyness <= 1.03]
    elif moneyness_filter == "OTM only":
        all_options = [o for o in all_options
                       if (o.call_put == "C" and o.moneyness > 1.03)
                       or (o.call_put == "P" and o.moneyness < 0.97)]
    elif moneyness_filter == "ITM only":
        all_options = [o for o in all_options
                       if (o.call_put == "C" and o.moneyness < 0.97)
                       or (o.call_put == "P" and o.moneyness > 1.03)]

    if not all_options:
        st.warning("No options matched your filters.")
        return

    # ── 3. Unusual Activity ────────────────────────────────────────────────────
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:10px;margin:20px 0 12px;">'
        f'<span style="font-size:1.3rem;">⚡</span>'
        f'<h2 style="margin:0;font-size:1.2rem;font-weight:700;color:{C_TEXT};">Unusual Activity</h2>'
        f'<span style="color:{C_TEXT3};font-size:0.85rem;">Top flow by volume / OI ratio</span></div>',
        unsafe_allow_html=True,
    )

    try:
        unusual = get_unusual_activity(all_options)[:5]
    except Exception:
        unusual = []

    if unusual:
        ua_cols = st.columns(min(len(unusual), 5))
        for i, opt in enumerate(unusual[:5]):
            ratio = _vol_oi_ratio(opt)
            cp_label = "CALL" if opt.call_put == "C" else "PUT"
            cp_color = C_HIGH if opt.call_put == "C" else C_LOW
            iv_str   = _iv_pct(opt.iv)
            with ua_cols[i]:
                st.markdown(
                    f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
                    f'border-top:3px solid {cp_color};border-radius:10px;padding:14px 16px;">'
                    f'<div style="display:flex;justify-content:space-between;'
                    f'align-items:center;margin-bottom:8px;">'
                    f'<span style="font-weight:800;font-size:1rem;color:{C_TEXT};">{opt.ticker}</span>'
                    f'<span style="background:{cp_color}22;color:{cp_color};font-size:0.7rem;'
                    f'font-weight:700;padding:2px 7px;border-radius:4px;">{cp_label}</span></div>'
                    f'<div style="font-size:1.15rem;font-weight:800;color:{C_TEXT};margin-bottom:4px;">'
                    f'${opt.strike:.1f}</div>'
                    f'<div style="font-size:0.75rem;color:{C_TEXT2};margin-bottom:8px;">'
                    f'Exp: {opt.expiry}</div>'
                    f'<div style="display:flex;justify-content:space-between;">'
                    f'<div><div style="font-size:0.65rem;color:{C_TEXT3};text-transform:uppercase;">Vol/OI</div>'
                    f'<div style="font-weight:700;color:{C_MOD};font-size:0.9rem;">{ratio:.2f}x</div></div>'
                    f'<div><div style="font-size:0.65rem;color:{C_TEXT3};text-transform:uppercase;">Volume</div>'
                    f'<div style="font-weight:700;color:{C_TEXT};font-size:0.9rem;">{opt.volume:,}</div></div>'
                    f'<div><div style="font-size:0.65rem;color:{C_TEXT3};text-transform:uppercase;">IV</div>'
                    f'<div style="font-weight:700;color:{C_ACCENT};font-size:0.9rem;">{iv_str}</div></div>'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )
    else:
        st.info("No unusual activity detected with current filters.")

    st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)

    # ── 4. Options Chain Table ─────────────────────────────────────────────────
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:10px;margin:0 0 12px;">'
        f'<span style="font-size:1.3rem;">📋</span>'
        f'<h2 style="margin:0;font-size:1.2rem;font-weight:700;color:{C_TEXT};">Options Chain</h2>'
        f'<span style="color:{C_TEXT3};font-size:0.85rem;">'
        f'{len(all_options)} contracts · scroll to explore</span></div>',
        unsafe_allow_html=True,
    )

    try:
        import pandas as pd

        rows = []
        for opt in all_options:
            rows.append({
                "Ticker":  opt.ticker,
                "Exp":     opt.expiry,
                "Strike":  f"${opt.strike:.1f}",
                "C/P":     opt.call_put,
                "Bid":     f"${opt.bid:.2f}",
                "Ask":     f"${opt.ask:.2f}",
                "Mid":     f"${_mid(opt):.2f}",
                "IV":      _iv_pct(opt.iv),
                "Delta":   f"{opt.delta:+.3f}",
                "Gamma":   f"{opt.gamma:.5f}",
                "Theta":   f"{opt.theta:+.3f}",
                "Vega":    f"{opt.vega:.4f}",
                "OI":      f"{opt.oi:,}",
                "Vol":     f"{opt.volume:,}",
                "Vol/OI":  f"{_vol_oi_ratio(opt):.2f}x",
                "Undl":    f"${opt.underlying_price:.2f}",
                "Money":   f"{opt.moneyness:.3f}",
            })

        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, height=340, hide_index=True)
    except Exception as e:
        st.error(f"Options table error: {e}")

    st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)

    # ── 5. IV Surface Heatmap ──────────────────────────────────────────────────
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:10px;margin:0 0 12px;">'
        f'<span style="font-size:1.3rem;">🌡️</span>'
        f'<h2 style="margin:0;font-size:1.2rem;font-weight:700;color:{C_TEXT};">IV Surface</h2>'
        f'<span style="color:{C_TEXT3};font-size:0.85rem;">'
        f'Implied volatility by strike and expiry</span></div>',
        unsafe_allow_html=True,
    )

    surf_ticker = st.selectbox(
        "Ticker for IV Surface",
        options=tickers_to_use,
        key="iv_surf_ticker",
    )

    try:
        surface = get_iv_surface(surf_ticker)
        z_pct   = [[round(v * 100, 1) for v in row] for row in surface["iv_grid"]]

        fig_surf = go.Figure(go.Heatmap(
            z=z_pct,
            x=[f"${s:.1f}" for s in surface["strikes"]],
            y=surface["expiries"],
            colorscale=[
                [0.00, "#1e3a5f"],
                [0.25, "#1d4ed8"],
                [0.50, "#0891b2"],
                [0.75, "#f59e0b"],
                [1.00, "#ef4444"],
            ],
            colorbar=dict(
                title="IV (%)",
                tickfont=dict(color=C_TEXT2),
                titlefont=dict(color=C_TEXT2),
            ),
            text=[[f"{v:.1f}%" for v in row] for row in z_pct],
            texttemplate="%{text}",
            textfont=dict(color="white", size=11),
            hovertemplate="Strike: %{x}<br>Expiry: %{y}<br>IV: %{z:.1f}%<extra></extra>",
        ))
        fig_surf.update_layout(
            **dark_layout(),
            title=dict(
                text=f"{surf_ticker} Implied Volatility Surface  ·  Spot ${surface['spot']:.2f}",
                font=dict(color=C_TEXT, size=14),
            ),
            xaxis=dict(title="Strike", color=C_TEXT2),
            yaxis=dict(title="Expiry", color=C_TEXT2),
            height=380,
        )
        st.plotly_chart(fig_surf, use_container_width=True)
    except Exception as e:
        st.error(f"IV surface error: {e}")

    st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)

    # ── 6. Max Pain Chart ──────────────────────────────────────────────────────
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:10px;margin:0 0 12px;">'
        f'<span style="font-size:1.3rem;">🎯</span>'
        f'<h2 style="margin:0;font-size:1.2rem;font-weight:700;color:{C_TEXT};">Max Pain Analysis</h2>'
        f'<span style="color:{C_TEXT3};font-size:0.85rem;">'
        f'Open interest by strike — calls vs puts</span></div>',
        unsafe_allow_html=True,
    )

    pain_ticker = st.selectbox(
        "Ticker for Max Pain",
        options=tickers_to_use,
        key="max_pain_ticker",
    )

    try:
        max_pain_strike = calculate_max_pain(all_options, pain_ticker)
        ticker_opts     = [o for o in all_options if o.ticker == pain_ticker]

        if ticker_opts:
            from collections import defaultdict
            call_oi_map: dict[float, int] = defaultdict(int)
            put_oi_map:  dict[float, int] = defaultdict(int)
            for opt in ticker_opts:
                if opt.call_put == "C":
                    call_oi_map[opt.strike] += opt.oi
                else:
                    put_oi_map[opt.strike] += opt.oi

            all_strikes  = sorted(set(call_oi_map) | set(put_oi_map))
            call_oi_vals = [call_oi_map.get(s, 0) for s in all_strikes]
            put_oi_vals  = [put_oi_map.get(s, 0)  for s in all_strikes]
            strike_labels = [f"${s:.1f}" for s in all_strikes]

            fig_pain = go.Figure()
            fig_pain.add_trace(go.Bar(
                x=strike_labels, y=call_oi_vals,
                name="Call OI", marker_color=C_HIGH, opacity=0.8,
            ))
            fig_pain.add_trace(go.Bar(
                x=strike_labels, y=put_oi_vals,
                name="Put OI", marker_color=C_LOW, opacity=0.8,
            ))

            mp_label = f"${max_pain_strike:.1f}"
            if mp_label in strike_labels:
                fig_pain.add_vline(
                    x=mp_label,
                    line_dash="dash", line_color=C_MOD, line_width=2,
                    annotation_text=f"Max Pain {mp_label}",
                    annotation_font_color=C_MOD,
                    annotation_position="top right",
                )

            fig_pain.update_layout(
                **dark_layout(),
                barmode="group",
                title=dict(
                    text=f"{pain_ticker} Open Interest by Strike  ·  Max Pain {mp_label}",
                    font=dict(color=C_TEXT, size=14),
                ),
                xaxis=dict(title="Strike", color=C_TEXT2, tickangle=-45),
                yaxis=dict(title="Open Interest", color=C_TEXT2),
                legend=dict(font=dict(color=C_TEXT2)),
                height=380,
            )
            st.plotly_chart(fig_pain, use_container_width=True)

            spot      = ticker_opts[0].underlying_price
            diff_pct  = ((max_pain_strike - spot) / spot * 100) if spot else 0.0
            diff_color = C_HIGH if diff_pct >= 0 else C_LOW
            st.markdown(
                f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
                f'border-radius:10px;padding:14px 20px;display:flex;gap:40px;margin-top:8px;">'
                f'<div><div style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;'
                f'margin-bottom:4px;">Max Pain Strike</div>'
                f'<div style="font-size:1.5rem;font-weight:800;color:{C_MOD};">${max_pain_strike:.2f}</div></div>'
                f'<div><div style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;'
                f'margin-bottom:4px;">Current Spot</div>'
                f'<div style="font-size:1.5rem;font-weight:800;color:{C_TEXT};">${spot:.2f}</div></div>'
                f'<div><div style="font-size:0.72rem;color:{C_TEXT3};text-transform:uppercase;'
                f'margin-bottom:4px;">Pain vs Spot</div>'
                f'<div style="font-size:1.5rem;font-weight:800;color:{diff_color};">'
                f'{diff_pct:+.1f}%</div></div></div>',
                unsafe_allow_html=True,
            )
        else:
            st.info(f"No options data for {pain_ticker} with current filters.")
    except Exception as e:
        st.error(f"Max pain error: {e}")

    st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

    # ── 7. Put/Call Ratio ──────────────────────────────────────────────────────
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:10px;margin:0 0 12px;">'
        f'<span style="font-size:1.3rem;">⚖️</span>'
        f'<h2 style="margin:0;font-size:1.2rem;font-weight:700;color:{C_TEXT};">Put / Call Ratio</h2>'
        f'<span style="color:{C_TEXT3};font-size:0.85rem;">'
        f'Sentiment gauge and historical trend</span></div>',
        unsafe_allow_html=True,
    )

    try:
        import numpy as np
        import pandas as pd
        from datetime import date, timedelta

        call_vol_total = sum(o.volume for o in all_options if o.call_put == "C")
        put_vol_total  = sum(o.volume for o in all_options if o.call_put == "P")
        call_oi_total  = sum(o.oi for o in all_options if o.call_put == "C")
        put_oi_total   = sum(o.oi for o in all_options if o.call_put == "P")

        pcr_vol = put_vol_total / call_vol_total if call_vol_total > 0 else 1.0
        pcr_oi  = put_oi_total  / call_oi_total  if call_oi_total  > 0 else 1.0

        if pcr_vol < 0.7:
            gauge_color     = C_HIGH
            sentiment_label = "Bullish"
        elif pcr_vol > 1.3:
            gauge_color     = C_LOW
            sentiment_label = "Bearish"
        else:
            gauge_color     = C_MOD
            sentiment_label = "Neutral"

        g1, g2 = st.columns([1, 2])

        with g1:
            fig_gauge = go.Figure(go.Indicator(
                mode="gauge+number+delta",
                value=round(pcr_vol, 2),
                delta=dict(reference=1.0, valueformat=".2f"),
                title=dict(text="P/C Ratio (Volume)", font=dict(color=C_TEXT, size=13)),
                gauge=dict(
                    axis=dict(range=[0, 2.5], tickcolor=C_TEXT2),
                    bar=dict(color=gauge_color),
                    bgcolor=C_SURFACE,
                    bordercolor=C_BORDER,
                    steps=[
                        dict(range=[0, 0.7],   color="#1a2e1a"),
                        dict(range=[0.7, 1.3],  color="#2a2a1a"),
                        dict(range=[1.3, 2.5],  color="#2e1a1a"),
                    ],
                    threshold=dict(
                        line=dict(color=C_TEXT2, width=2),
                        thickness=0.75,
                        value=1.0,
                    ),
                ),
                number=dict(font=dict(color=C_TEXT)),
            ))
            fig_gauge.update_layout(**dark_layout(), height=280)
            st.plotly_chart(fig_gauge, use_container_width=True)

            st.markdown(
                f'<div style="text-align:center;margin-top:-12px;">'
                f'<span style="font-size:1.1rem;font-weight:700;color:{gauge_color};">'
                f'{sentiment_label}</span>'
                f'<span style="color:{C_TEXT3};font-size:0.8rem;margin-left:8px;">'
                f'OI ratio: {pcr_oi:.2f}</span></div>',
                unsafe_allow_html=True,
            )

        with g2:
            rng_pcr = np.random.default_rng(seed=99)
            n_days  = 60
            hist_pcr = np.clip(
                pcr_vol + np.cumsum(rng_pcr.normal(0, 0.05, n_days)) * 0.3,
                0.3, 2.5,
            ).tolist()
            dates = [(date.today() - timedelta(days=n_days - i)).isoformat()
                     for i in range(n_days)]

            fig_pcr_hist = go.Figure()
            fig_pcr_hist.add_trace(go.Scatter(
                x=dates, y=hist_pcr,
                mode="lines",
                line=dict(color=C_ACCENT, width=2),
                fill="tozeroy",
                fillcolor=f"{C_ACCENT}22",
                name="P/C Ratio",
                hovertemplate="Date: %{x}<br>P/C: %{y:.2f}<extra></extra>",
            ))
            fig_pcr_hist.add_hline(y=1.0, line_dash="dot", line_color=C_TEXT3,
                                   annotation_text="Neutral 1.0",
                                   annotation_font_color=C_TEXT3)
            fig_pcr_hist.add_hline(y=1.3, line_dash="dash", line_color=C_LOW, line_width=1)
            fig_pcr_hist.add_hline(y=0.7, line_dash="dash", line_color=C_HIGH, line_width=1)
            fig_pcr_hist.update_layout(
                **dark_layout(),
                title=dict(text="60-Day P/C Volume Ratio History",
                           font=dict(color=C_TEXT, size=13)),
                xaxis=dict(color=C_TEXT2, showgrid=False),
                yaxis=dict(color=C_TEXT2, range=[0, 2.5]),
                showlegend=False,
                height=280,
            )
            st.plotly_chart(fig_pcr_hist, use_container_width=True)

    except Exception as e:
        st.error(f"P/C ratio error: {e}")

    st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

    # ── 8. Strategy Screener ───────────────────────────────────────────────────
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:10px;margin:0 0 12px;">'
        f'<span style="font-size:1.3rem;">🧩</span>'
        f'<h2 style="margin:0;font-size:1.2rem;font-weight:700;color:{C_TEXT};">Strategy Screener</h2>'
        f'<span style="color:{C_TEXT3};font-size:0.85rem;">'
        f'Covered calls · Protective puts · Straddles</span></div>',
        unsafe_allow_html=True,
    )

    try:
        ideas = _strategy_ideas(all_options)

        if not ideas:
            st.info("No strategy opportunities matched current filters.")
        else:
            cols = st.columns(3)
            for i, idea in enumerate(ideas):
                c = idea["color"]
                with cols[i % 3]:
                    st.markdown(
                        f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
                        f'border-left:4px solid {c};border-radius:10px;'
                        f'padding:14px 16px;margin-bottom:12px;">'
                        f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">'
                        f'<span style="font-size:1.1rem;">{idea["icon"]}</span>'
                        f'<span style="font-weight:700;font-size:0.9rem;color:{c};">{idea["type"]}</span>'
                        f'</div>'
                        f'<div style="font-size:1rem;font-weight:800;color:{C_TEXT};margin-bottom:4px;">'
                        f'{idea["ticker"]} ${idea["strike"]:.1f}</div>'
                        f'<div style="font-size:0.75rem;color:{C_TEXT2};margin-bottom:8px;">'
                        f'Exp: {idea["expiry"]}</div>'
                        f'<div style="font-size:0.78rem;color:{C_TEXT3};line-height:1.4;">'
                        f'{idea["rationale"]}</div>'
                        f'<div style="margin-top:8px;background:{c}18;border-radius:6px;'
                        f'padding:4px 8px;display:inline-block;">'
                        f'<span style="font-size:0.72rem;color:{c};font-weight:700;">'
                        f'IV {idea["iv_pct"]:.0f}%</span></div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
    except Exception as e:
        st.error(f"Strategy screener error: {e}")

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
