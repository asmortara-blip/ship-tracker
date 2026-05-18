"""
Options Screener Tab — Derivatives Flow & Volatility Intelligence

Sections:
  1. Page header
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

from data.quality import DataSource
from ui.styles import (
    C_ACCENT,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    apply_dark_layout,
    badge,
    insight_card_html,
    metric_card_row,
    page_header,
    section_header,
    source_footer,
)

_ALL_TICKERS = ["ZIM", "MATX", "DAC", "SBLK", "STNG", "GSL"]

# Mock data — flag every figure so consumers don't trust the prints
_OPTIONS_SRC = DataSource.demo("Shipping Equity Options (synthetic chain)")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mid(opt) -> float:
    return round((opt.bid + opt.ask) / 2.0, 2)


def _vol_oi_ratio(opt) -> float:
    return round(opt.volume / opt.oi, 2) if opt.oi > 0 else 0.0


def _iv_pct(iv: float) -> str:
    return f"{iv * 100:.1f}%"


def _strategy_ideas(options: list) -> list[dict]:
    """
    Identify simple strategy opportunities based on IV levels and moneyness.
    Returns list of dicts with: type, ticker, expiry, strike, iv_pct, rationale,
    color, category.
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
                "type":      "Covered Call",
                "ticker":    opt.ticker,
                "expiry":    opt.expiry,
                "strike":    opt.strike,
                "iv_pct":    iv_pct,
                "rationale": f"IV {iv_pct:.0f}% — elevated premium, delta {opt.delta:.2f}",
                "color":     C_HIGH,
                "category":  "PORT_DEMAND",  # maps to green in CATEGORY_COLORS
                "score":     min(0.95, 0.55 + iv_pct / 200.0),
                "action":    "Prioritize",
            })

        # Protective put: put with IV < 70%, delta < -0.30, moderate OI
        elif (opt.call_put == "P"
              and iv_pct < 70
              and opt.delta <= -0.30
              and opt.oi >= 300):
            ideas.append({
                "type":      "Protective Put",
                "ticker":    opt.ticker,
                "expiry":    opt.expiry,
                "strike":    opt.strike,
                "iv_pct":    iv_pct,
                "rationale": f"Cheap downside hedge — IV {iv_pct:.0f}%, delta {opt.delta:.2f}",
                "color":     C_MOD,
                "category":  "MACRO",
                "score":     min(0.85, 0.45 + (70 - iv_pct) / 100.0),
                "action":    "Monitor",
            })

        # Straddle candidate: near ATM with high IV
        elif (0.98 <= opt.moneyness <= 1.02
              and iv_pct > 75
              and opt.oi >= 200):
            ideas.append({
                "type":      "Straddle",
                "ticker":    opt.ticker,
                "expiry":    opt.expiry,
                "strike":    opt.strike,
                "iv_pct":    iv_pct,
                "rationale": f"High IV {iv_pct:.0f}% at ATM — volatility expansion play",
                "color":     C_ACCENT,
                "category":  "ROUTE",
                "score":     min(0.9, 0.5 + iv_pct / 250.0),
                "action":    "Watch",
            })

        if len(ideas) >= 12:
            break

    return ideas


# ── Main render ───────────────────────────────────────────────────────────────

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

    # ── 1. Page header ────────────────────────────────────────────────────────
    page_header(
        title="Options Screener",
        subtitle="Derivatives flow & volatility intelligence — shipping equity options",
        badge_text="OPTIONS",
        badge_color=C_ACCENT,
    )

    # ── 2. Filter controls ────────────────────────────────────────────────────
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


    # ── Load data ─────────────────────────────────────────────────────────────
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

    # ── 3. Unusual Activity ───────────────────────────────────────────────────
    section_header("Unusual Activity", "Top flow by volume / OI ratio")

    try:
        unusual = get_unusual_activity(all_options)[:5]
    except Exception:
        unusual = []

    if unusual:
        ua_metrics = []
        for opt in unusual[:5]:
            ratio    = _vol_oi_ratio(opt)
            cp_label = "CALL" if opt.call_put == "C" else "PUT"
            cp_color = C_HIGH if opt.call_put == "C" else C_LOW
            iv_str   = _iv_pct(opt.iv)
            ua_metrics.append({
                "label":    f"{opt.ticker} · {cp_label}",
                "value":    f"${opt.strike:.1f}",
                "delta":    f"Vol/OI {ratio:.2f}x  ·  IV {iv_str}",
                "sublabel": f"Exp {opt.expiry}  ·  Vol {opt.volume:,}",
                "accent":   cp_color,
            })
        metric_card_row(ua_metrics, columns=min(len(ua_metrics), 5))
        st.markdown(source_footer([_OPTIONS_SRC]), unsafe_allow_html=True)
    else:
        st.info("No unusual activity detected with current filters.")

    # ── 4. Options Chain Table ────────────────────────────────────────────────
    section_header(
        "Options Chain",
        f"{len(all_options)} contracts · scroll to explore",
    )

    try:
        import pandas as pd

        rows = []
        for opt in all_options:
            rows.append({
                "Ticker": opt.ticker,
                "Exp":    opt.expiry,
                "Strike": f"${opt.strike:.1f}",
                "C/P":    opt.call_put,
                "Bid":    f"${opt.bid:.2f}",
                "Ask":    f"${opt.ask:.2f}",
                "Mid":    f"${_mid(opt):.2f}",
                "IV":     _iv_pct(opt.iv),
                "Delta":  f"{opt.delta:+.3f}",
                "Gamma":  f"{opt.gamma:.5f}",
                "Theta":  f"{opt.theta:+.3f}",
                "Vega":   f"{opt.vega:.4f}",
                "OI":     f"{opt.oi:,}",
                "Vol":    f"{opt.volume:,}",
                "Vol/OI": f"{_vol_oi_ratio(opt):.2f}x",
                "Undl":   f"${opt.underlying_price:.2f}",
                "Money":  f"{opt.moneyness:.3f}",
            })

        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, height=340, hide_index=True)
        st.markdown(source_footer([_OPTIONS_SRC]), unsafe_allow_html=True)
    except Exception as e:
        st.error(f"Options table error: {e}")

    # ── 5. IV Surface Heatmap ─────────────────────────────────────────────────
    section_header("IV Surface", "Implied volatility by strike and expiry")

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
                [0.75, "#c9962b"],
                [1.00, "#c0392b"],
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
        apply_dark_layout(
            fig_surf,
            title=f"{surf_ticker} Implied Volatility Surface  ·  Spot ${surface['spot']:.2f}",
            height=380,
            showlegend=False,
        )
        fig_surf.update_layout(
            xaxis=dict(title="Strike", color=C_TEXT2),
            yaxis=dict(title="Expiry", color=C_TEXT2),
        )
        st.plotly_chart(fig_surf, use_container_width=True)
        st.markdown(source_footer([_OPTIONS_SRC]), unsafe_allow_html=True)
    except Exception as e:
        st.error(f"IV surface error: {e}")

    # ── 6. Max Pain Chart ─────────────────────────────────────────────────────
    section_header("Max Pain Analysis", "Open interest by strike — calls vs puts")

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

            apply_dark_layout(
                fig_pain,
                title=f"{pain_ticker} Open Interest by Strike  ·  Max Pain {mp_label}",
                height=380,
            )
            fig_pain.update_layout(
                barmode="group",
                xaxis=dict(title="Strike", color=C_TEXT2, tickangle=-45),
                yaxis=dict(title="Open Interest", color=C_TEXT2),
                legend=dict(font=dict(color=C_TEXT2)),
            )
            st.plotly_chart(fig_pain, use_container_width=True)
            st.markdown(source_footer([_OPTIONS_SRC]), unsafe_allow_html=True)

            spot       = ticker_opts[0].underlying_price
            diff_pct   = ((max_pain_strike - spot) / spot * 100) if spot else 0.0
            diff_color = C_HIGH if diff_pct >= 0 else C_LOW
            metric_card_row(
                [
                    {"label": "Max Pain Strike",
                     "value": f"${max_pain_strike:.2f}",
                     "accent": C_MOD},
                    {"label": "Current Spot",
                     "value": f"${spot:.2f}",
                     "accent": C_TEXT2},
                    {"label": "Pain vs Spot",
                     "value": f"{diff_pct:+.1f}%",
                     "accent": diff_color},
                ],
                columns=3,
            )
        else:
            st.info(f"No options data for {pain_ticker} with current filters.")
    except Exception as e:
        st.error(f"Max pain error: {e}")

    # ── 7. Put/Call Ratio ─────────────────────────────────────────────────────
    section_header("Put / Call Ratio", "Sentiment gauge and historical trend")

    try:
        import numpy as np
        import pandas as pd  # noqa: F401  (kept for ecosystem compatibility)
        from datetime import date, timedelta

        call_vol_total = sum(o.volume for o in all_options if o.call_put == "C")
        put_vol_total  = sum(o.volume for o in all_options if o.call_put == "P")
        call_oi_total  = sum(o.oi     for o in all_options if o.call_put == "C")
        put_oi_total   = sum(o.oi     for o in all_options if o.call_put == "P")

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
                title=dict(text="P/C Ratio (Volume)",
                           font=dict(color=C_TEXT, size=13)),
                gauge=dict(
                    axis=dict(range=[0, 2.5], tickcolor=C_TEXT2),
                    bar=dict(color=gauge_color),
                    bgcolor="#12151e",
                    bordercolor="rgba(232,230,225,0.05)",
                    steps=[
                        dict(range=[0, 0.7],   color="#1a2e1a"),
                        dict(range=[0.7, 1.3], color="#2a2a1a"),
                        dict(range=[1.3, 2.5], color="#2e1a1a"),
                    ],
                    threshold=dict(
                        line=dict(color=C_TEXT2, width=2),
                        thickness=0.75,
                        value=1.0,
                    ),
                ),
                number=dict(font=dict(color=C_TEXT)),
            ))
            apply_dark_layout(fig_gauge, height=280, showlegend=False)
            st.plotly_chart(fig_gauge, use_container_width=True)

            sentiment_html = (
                badge(sentiment_label, color=gauge_color)
                + f'<span style="color:{C_TEXT3};font-size:0.8rem;margin-left:10px;'
                f'font-family:var(--mono);">OI ratio: {pcr_oi:.2f}</span>'
            )
            st.markdown(sentiment_html, unsafe_allow_html=True)

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
            fig_pcr_hist.add_hline(y=1.3, line_dash="dash",
                                   line_color=C_LOW, line_width=1)
            fig_pcr_hist.add_hline(y=0.7, line_dash="dash",
                                   line_color=C_HIGH, line_width=1)
            apply_dark_layout(
                fig_pcr_hist,
                title="60-Day P/C Volume Ratio History",
                height=280,
                showlegend=False,
            )
            fig_pcr_hist.update_layout(
                xaxis=dict(color=C_TEXT2, showgrid=False),
                yaxis=dict(color=C_TEXT2, range=[0, 2.5]),
            )
            st.plotly_chart(fig_pcr_hist, use_container_width=True)

        st.markdown(source_footer([_OPTIONS_SRC]), unsafe_allow_html=True)

    except Exception as e:
        st.error(f"P/C ratio error: {e}")

    # ── 8. Strategy Screener ──────────────────────────────────────────────────
    section_header(
        "Strategy Screener",
        "Covered calls · Protective puts · Straddles",
    )

    try:
        ideas = _strategy_ideas(all_options)

        if not ideas:
            st.info("No strategy opportunities matched current filters.")
        else:
            cols = st.columns(3)
            for i, idea in enumerate(ideas):
                with cols[i % 3]:
                    title = f'{idea["type"]} · {idea["ticker"]} ${idea["strike"]:.1f}'
                    rationale = (
                        f'Exp {idea["expiry"]} — {idea["rationale"]}'
                    )
                    st.markdown(
                        insight_card_html(
                            title=title,
                            score=idea["score"],
                            action=idea["action"],
                            rationale=rationale,
                            category=idea["category"],
                        ),
                        unsafe_allow_html=True,
                    )
            st.markdown(source_footer([_OPTIONS_SRC]), unsafe_allow_html=True)
    except Exception as e:
        st.error(f"Strategy screener error: {e}")

