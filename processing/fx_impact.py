"""fx_impact.py — FX/currency impact analysis on shipping economics.

Translates exchange rate movements into directional shipping signals,
identifies affected routes and stocks, and renders a Streamlit dashboard
panel with a currency matrix, signal cards, and a DXY-proxy gauge.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
from loguru import logger

from data.currency_feed import KEY_CURRENCIES


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class FXSignal:
    pair: str                       # e.g. "USD/CNY"
    pair_name: str                  # human-readable name
    current_rate: float
    change_30d_pct: float           # positive = USD strengthened
    direction: str                  # "USD_STRENGTHENING" | "USD_WEAKENING" | "STABLE"
    shipping_impact: str            # one-liner for display
    affected_routes: list[str] = field(default_factory=list)
    affected_stocks: list[str] = field(default_factory=list)
    signal: str = "NEUTRAL"         # "BULLISH_FOR_SHIPPING" | "BEARISH_FOR_SHIPPING" | "NEUTRAL"
    magnitude: float = 0.0          # [0, 1]
    trade_idea: str = ""


# ---------------------------------------------------------------------------
# FX → shipping logic tables
# ---------------------------------------------------------------------------

# Each entry:  (direction_condition, signal, routes, stocks, trade_idea_template)
# direction_condition: "STRENGTHENING" or "WEAKENING"
_PAIR_LOGIC: dict[str, dict] = {
    "USD/CNY": {
        "STRENGTHENING": {
            "signal": "BULLISH_FOR_SHIPPING",
            "routes": ["transpacific_eb", "sea_transpacific_eb"],
            "stocks": ["ZIM", "MATX", "DAC"],
            "trade_idea": (
                "Stronger USD vs CNY makes US imports from China cheaper in dollar terms, "
                "boosting Trans-Pacific eastbound demand. Watch ZIM/MATX for rate uplift. "
                "Caveat: prolonged USD strength raises trade war risk — hedge with options."
            ),
        },
        "WEAKENING": {
            "signal": "BEARISH_FOR_SHIPPING",
            "routes": ["transpacific_eb"],
            "stocks": ["ZIM", "MATX"],
            "trade_idea": (
                "Yuan appreciation makes Chinese goods more expensive for US importers, "
                "compressing Trans-Pacific EB volumes. Consider underweight MATX/ZIM "
                "relative to transatlantic peers."
            ),
        },
    },
    "USD/EUR": {
        "STRENGTHENING": {
            "signal": "BULLISH_FOR_SHIPPING",
            "routes": ["transatlantic"],
            "stocks": ["ZIM", "DAC", "CMRE"],
            "trade_idea": (
                "Stronger USD makes US exports cheaper for European buyers, lifting "
                "transatlantic westbound volumes. DAC and CMRE with Atlantic exposure "
                "are beneficiaries."
            ),
        },
        "WEAKENING": {
            "signal": "BEARISH_FOR_SHIPPING",
            "routes": ["transatlantic"],
            "stocks": ["DAC", "CMRE"],
            "trade_idea": (
                "Weaker USD vs EUR reduces competitiveness of US exports to Europe, "
                "softening transatlantic WB utilisation. Monitor DAC charter rates."
            ),
        },
    },
    "USD/KRW": {
        "STRENGTHENING": {
            "signal": "BULLISH_FOR_SHIPPING",
            "routes": ["asia_europe", "transpacific_eb"],
            "stocks": ["SBLK", "DAC"],
            "trade_idea": (
                "Weaker Korean Won reduces newbuild and repair costs for non-Korean buyers, "
                "potentially accelerating fleet expansion. Near-term dry bulk operators "
                "benefit from lower opex if Korean sourcing is significant."
            ),
        },
        "WEAKENING": {
            "signal": "NEUTRAL",
            "routes": [],
            "stocks": [],
            "trade_idea": (
                "Stronger Won raises Korean shipbuilder costs in USD terms, "
                "which can slow orderbook growth — mildly supportive of existing fleet values."
            ),
        },
    },
    "USD/JPY": {
        "STRENGTHENING": {
            "signal": "BULLISH_FOR_SHIPPING",
            "routes": ["transpacific_eb", "asia_europe"],
            "stocks": ["SBLK", "ZIM"],
            "trade_idea": (
                "Weak yen makes Japanese exports highly competitive globally, driving "
                "volume growth on Asia outbound lanes. SBLK benefits via bulk commodities; "
                "ZIM via electronics/auto components."
            ),
        },
        "WEAKENING": {
            "signal": "NEUTRAL",
            "routes": [],
            "stocks": [],
            "trade_idea": (
                "Yen strengthening moderates Japanese export competitiveness — "
                "slight headwind for Asia outbound volumes but not a primary shipping driver."
            ),
        },
    },
    "USD/BRL": {
        "STRENGTHENING": {
            "signal": "BULLISH_FOR_SHIPPING",
            "routes": ["china_south_america", "us_east_south_america"],
            "stocks": ["SBLK", "CMRE"],
            "trade_idea": (
                "Weaker Real makes Brazilian agricultural and commodity exports cheaper "
                "in USD terms, boosting soy/iron ore shipments to China and Asia. "
                "SBLK (dry bulk) is a direct beneficiary."
            ),
        },
        "WEAKENING": {
            "signal": "BEARISH_FOR_SHIPPING",
            "routes": ["china_south_america"],
            "stocks": ["SBLK"],
            "trade_idea": (
                "Real appreciation raises cost of Brazilian exports, potentially shifting "
                "soy sourcing toward US/Argentina — reduces South America dry bulk tonne-miles."
            ),
        },
    },
    "USD/SGD": {
        "STRENGTHENING": {
            "signal": "NEUTRAL",
            "routes": ["asia_europe", "transpacific_eb"],
            "stocks": ["ZIM", "DAC"],
            "trade_idea": (
                "Weaker SGD modestly reduces SE Asia hub operating costs for carriers "
                "with Singapore port call exposure, a small margin tailwind."
            ),
        },
        "WEAKENING": {
            "signal": "NEUTRAL",
            "routes": [],
            "stocks": [],
            "trade_idea": (
                "Stronger SGD raises hub costs slightly — immaterial for most carriers "
                "but watch for impact on Singapore-listed shipping entities."
            ),
        },
    },
}

# Thresholds
_SIGNAL_THRESHOLD_PCT = 3.0    # |change| > 3% triggers a signal
_STRONG_THRESHOLD_PCT = 6.0    # |change| > 6% = strong signal


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

def analyze_fx_signals(
    fx_rates: dict[str, float],
    fx_history: dict[str, pd.DataFrame],
) -> list[FXSignal]:
    """Translate FX rate movements into shipping-specific signals.

    Parameters
    ----------
    fx_rates:
        Current spot rates, e.g. {"USD/CNY": 7.24, ...}
    fx_history:
        Historical DataFrames per pair with columns (date, close, pair).

    Returns
    -------
    List of FXSignal, one per key currency pair.
    """
    signals: list[FXSignal] = []

    for pair, meta in KEY_CURRENCIES.items():
        current_rate = fx_rates.get(pair)
        if current_rate is None:
            logger.debug(f"No current rate for {pair} — skipping")
            continue

        # --- 30-day change --------------------------------------------------
        change_30d_pct = _compute_30d_change(pair, fx_history)

        # --- Direction ------------------------------------------------------
        if abs(change_30d_pct) < _SIGNAL_THRESHOLD_PCT:
            direction = "STABLE"
        elif change_30d_pct > 0:
            direction = "USD_STRENGTHENING"
        else:
            direction = "USD_WEAKENING"

        # --- Magnitude [0, 1] -----------------------------------------------
        abs_chg = abs(change_30d_pct)
        if abs_chg >= _STRONG_THRESHOLD_PCT:
            magnitude = min(1.0, 0.7 + (abs_chg - _STRONG_THRESHOLD_PCT) / 10.0)
        elif abs_chg >= _SIGNAL_THRESHOLD_PCT:
            magnitude = 0.3 + (abs_chg - _SIGNAL_THRESHOLD_PCT) / (_STRONG_THRESHOLD_PCT - _SIGNAL_THRESHOLD_PCT) * 0.4
        else:
            magnitude = abs_chg / _SIGNAL_THRESHOLD_PCT * 0.3

        magnitude = round(max(0.0, min(1.0, magnitude)), 3)

        # --- Logic lookup ---------------------------------------------------
        pair_rules = _PAIR_LOGIC.get(pair, {})
        direction_key = (
            "STRENGTHENING" if direction == "USD_STRENGTHENING"
            else "WEAKENING" if direction == "USD_WEAKENING"
            else None
        )

        if direction_key and direction_key in pair_rules:
            rule = pair_rules[direction_key]
            signal = rule["signal"] if direction != "STABLE" else "NEUTRAL"
            affected_routes = rule["routes"]
            affected_stocks = rule["stocks"]
            trade_idea = rule["trade_idea"]
        else:
            signal = "NEUTRAL"
            affected_routes = []
            affected_stocks = []
            trade_idea = f"{pair} is stable; no material shipping signal at this time."

        if direction == "STABLE":
            signal = "NEUTRAL"

        signals.append(FXSignal(
            pair=pair,
            pair_name=meta["name"],
            current_rate=current_rate,
            change_30d_pct=round(change_30d_pct, 2),
            direction=direction,
            shipping_impact=meta["shipping_impact"],
            affected_routes=affected_routes,
            affected_stocks=affected_stocks,
            signal=signal,
            magnitude=magnitude,
            trade_idea=trade_idea,
        ))

    logger.info(
        f"FX signals: {sum(1 for s in signals if s.signal == 'BULLISH_FOR_SHIPPING')} bullish, "
        f"{sum(1 for s in signals if s.signal == 'BEARISH_FOR_SHIPPING')} bearish, "
        f"{sum(1 for s in signals if s.signal == 'NEUTRAL')} neutral"
    )
    return signals


def get_fx_composite_signal(signals: list[FXSignal]) -> dict:
    """Aggregate individual FX signals into a single composite view.

    Returns
    -------
    dict with keys:
        net_shipping_signal: "BULLISH" | "BEARISH" | "NEUTRAL"
        bullish_count: int
        bearish_count: int
        key_driver: str  — pair name of the highest-magnitude signal
        summary: str     — one-paragraph human-readable summary
    """
    bullish = [s for s in signals if s.signal == "BULLISH_FOR_SHIPPING"]
    bearish = [s for s in signals if s.signal == "BEARISH_FOR_SHIPPING"]

    bullish_count = len(bullish)
    bearish_count = len(bearish)

    if bullish_count > bearish_count:
        net = "BULLISH"
    elif bearish_count > bullish_count:
        net = "BEARISH"
    else:
        net = "NEUTRAL"

    # Key driver = highest magnitude signal
    key_driver = ""
    if signals:
        top = max(signals, key=lambda s: s.magnitude)
        key_driver = f"{top.pair} ({top.direction.replace('_', ' ').title()}, {top.change_30d_pct:+.1f}% 30d)"

    # Build summary
    if not signals:
        summary = "No FX data available. Unable to assess currency impact on shipping economics."
    elif net == "NEUTRAL" and bullish_count == 0 and bearish_count == 0:
        summary = (
            "Major currency pairs are broadly stable vs the US dollar. "
            "No material FX-driven shipping signal at this time. "
            "Monitor USD/CNY and USD/BRL for emerging trade flow shifts."
        )
    else:
        parts = []
        if bullish:
            pairs_str = ", ".join(s.pair for s in bullish)
            parts.append(f"{pairs_str} moves are supportive of shipping volumes")
        if bearish:
            pairs_str = ", ".join(s.pair for s in bearish)
            parts.append(f"{pairs_str} moves create headwinds")
        body = "; ".join(parts) + "."
        summary = (
            f"Currency analysis shows a {net.lower()} net shipping signal. "
            f"{body} "
            f"Key driver: {key_driver}."
        )

    return {
        "net_shipping_signal": net,
        "bullish_count": bullish_count,
        "bearish_count": bearish_count,
        "key_driver": key_driver,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Streamlit rendering
# ---------------------------------------------------------------------------

def render_fx_panel(
    fx_rates: dict[str, float],
    fx_history: dict[str, pd.DataFrame],
    stock_data: dict,
) -> None:
    """Render the FX/Currency Analysis panel in Streamlit.

    Sections
    --------
    1. Currency matrix (2x3 grid) — pair, rate, 30d change, impact text
    2. FX signal cards — trade ideas for significant moves
    3. DXY proxy gauge — USD strength index vs major pairs
    """
    try:
        import streamlit as st
    except ImportError:
        logger.error("streamlit not available — cannot render FX panel")
        return

    st.subheader("Currency & FX Impact on Shipping")

    signals = analyze_fx_signals(fx_rates, fx_history)
    composite = get_fx_composite_signal(signals)

    # --- Composite banner --------------------------------------------------
    net = composite["net_shipping_signal"]
    banner_color = {"BULLISH": "#27ae60", "BEARISH": "#e74c3c", "NEUTRAL": "#7f8c8d"}.get(net, "#7f8c8d")
    st.markdown(
        f"""
        <div style="background:{banner_color};color:#fff;padding:10px 16px;
                    border-radius:6px;margin-bottom:12px;">
            <b>FX Net Signal: {net}</b> &nbsp;·&nbsp;
            {composite['bullish_count']} bullish / {composite['bearish_count']} bearish &nbsp;·&nbsp;
            Key driver: {composite['key_driver'] or '—'}
        </div>
        """,
        unsafe_allow_html=True,
    )

    # --- Section 1: Currency matrix (2 rows × 3 cols) ----------------------
    st.markdown("#### Currency Matrix")
    pairs = list(KEY_CURRENCIES.keys())
    signal_by_pair = {s.pair: s for s in signals}

    for row_start in range(0, len(pairs), 3):
        cols = st.columns(3)
        for col_idx, pair in enumerate(pairs[row_start: row_start + 3]):
            sig = signal_by_pair.get(pair)
            with cols[col_idx]:
                if sig is None:
                    st.metric(label=pair, value="N/A")
                    continue

                chg = sig.change_30d_pct
                arrow = "▲" if chg > 0 else "▼" if chg < 0 else "—"
                chg_color = "#27ae60" if chg > 0 else "#e74c3c" if chg < 0 else "#7f8c8d"

                rate_display = _format_rate(pair, sig.current_rate)
                impact_text = sig.shipping_impact

                st.markdown(
                    f"""
                    <div style="border:1px solid #ddd;border-radius:8px;padding:12px;
                                background:#fafafa;height:130px;">
                        <div style="font-weight:600;font-size:13px;color:#2c3e50;">{pair}</div>
                        <div style="font-size:22px;font-weight:700;margin:4px 0;">{rate_display}</div>
                        <div style="color:{chg_color};font-size:14px;font-weight:600;">
                            {arrow} {abs(chg):.2f}% (30d)
                        </div>
                        <div style="font-size:11px;color:#666;margin-top:4px;">{impact_text}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

    # --- Section 2: FX signal cards ----------------------------------------
    significant = [s for s in signals if s.signal != "NEUTRAL" and s.magnitude >= 0.3]
    if significant:
        st.markdown("#### FX Signal Cards")
        for sig in sorted(significant, key=lambda s: s.magnitude, reverse=True):
            card_color = "#e8f8f0" if sig.signal == "BULLISH_FOR_SHIPPING" else "#fdecea"
            border_color = "#27ae60" if sig.signal == "BULLISH_FOR_SHIPPING" else "#e74c3c"
            signal_label = "BULLISH FOR SHIPPING" if sig.signal == "BULLISH_FOR_SHIPPING" else "BEARISH FOR SHIPPING"

            routes_str = ", ".join(sig.affected_routes) if sig.affected_routes else "—"
            stocks_str = ", ".join(sig.affected_stocks) if sig.affected_stocks else "—"
            magnitude_bar = "█" * round(sig.magnitude * 10) + "░" * (10 - round(sig.magnitude * 10))

            st.markdown(
                f"""
                <div style="border-left:4px solid {border_color};background:{card_color};
                            border-radius:6px;padding:14px 16px;margin-bottom:10px;">
                    <div style="font-weight:700;font-size:14px;color:#2c3e50;">
                        {sig.pair} — {sig.pair_name}
                        <span style="float:right;color:{border_color};font-size:12px;">
                            {signal_label}
                        </span>
                    </div>
                    <div style="font-size:12px;color:#555;margin:6px 0;">
                        Rate: <b>{_format_rate(sig.pair, sig.current_rate)}</b> &nbsp;|&nbsp;
                        30d change: <b>{sig.change_30d_pct:+.2f}%</b> &nbsp;|&nbsp;
                        Direction: <b>{sig.direction.replace('_', ' ').title()}</b>
                    </div>
                    <div style="font-size:11px;color:#777;font-family:monospace;">
                        Magnitude: [{magnitude_bar}] {sig.magnitude:.2f}
                    </div>
                    <div style="font-size:12px;margin-top:8px;color:#333;">
                        <b>Affected routes:</b> {routes_str}<br>
                        <b>Affected stocks:</b> {stocks_str}
                    </div>
                    <div style="font-size:12px;margin-top:8px;border-top:1px solid #ddd;
                                padding-top:8px;color:#444;">
                        <b>Trade idea:</b> {sig.trade_idea}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
    else:
        st.info("No significant FX signals at this time (all pairs within ±3% over 30 days).")

    # --- Section 3: DXY proxy gauge ----------------------------------------
    st.markdown("#### USD Strength Index (DXY Proxy)")
    dxy_value, dxy_label, dxy_color = _compute_dxy_proxy(signals)

    col_gauge, col_desc = st.columns([1, 2])
    with col_gauge:
        st.markdown(
            f"""
            <div style="text-align:center;padding:16px;border:2px solid {dxy_color};
                        border-radius:50%;width:110px;height:110px;margin:auto;
                        display:flex;flex-direction:column;justify-content:center;">
                <div style="font-size:28px;font-weight:700;color:{dxy_color};">{dxy_value:+.1f}%</div>
                <div style="font-size:10px;color:#666;">30d avg chg</div>
            </div>
            <div style="text-align:center;margin-top:8px;font-weight:600;
                        font-size:13px;color:{dxy_color};">{dxy_label}</div>
            """,
            unsafe_allow_html=True,
        )
    with col_desc:
        _render_dxy_interpretation(dxy_value, dxy_label, signals)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _compute_30d_change(pair: str, fx_history: dict[str, pd.DataFrame]) -> float:
    """Return 30-day % change for a pair. Positive = USD strengthened."""
    hist = fx_history.get(pair)
    if hist is None or hist.empty or "close" not in hist.columns:
        return 0.0

    df = hist.sort_values("date").dropna(subset=["close"])
    if len(df) < 2:
        return 0.0

    current = float(df["close"].iloc[-1])

    # Find row closest to 30 days ago
    if "date" in df.columns:
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
        cutoff = df["date"].iloc[-1] - pd.Timedelta(days=30)
        older = df[df["date"] <= cutoff]
        if older.empty:
            older_val = float(df["close"].iloc[0])
        else:
            older_val = float(older["close"].iloc[-1])
    else:
        older_val = float(df["close"].iloc[max(0, len(df) - 31)])

    if older_val == 0:
        return 0.0

    # For USD/EUR: stored as USD/EUR (how many USD per EUR).
    # A rise in USD/EUR means MORE USD per EUR → EUR strengthening → USD weakening.
    # Invert sign so that positive always means USD strengthening.
    if pair == "USD/EUR":
        return round((older_val - current) / older_val * 100, 4)

    return round((current - older_val) / older_val * 100, 4)


def _compute_dxy_proxy(signals: list[FXSignal]) -> tuple[float, str, str]:
    """Compute a simple DXY proxy as the average 30d change across pairs.

    USD/EUR is excluded from this proxy since a rising USD/EUR means a weakening
    USD (EUR/USD convention inverted). The stored change_30d_pct already has
    the sign corrected so we can average directly.

    Returns (avg_change_pct, label, color).
    """
    if not signals:
        return 0.0, "NEUTRAL", "#7f8c8d"

    values = [s.change_30d_pct for s in signals]
    avg = sum(values) / len(values) if values else 0.0

    if avg > 3.0:
        label, color = "USD STRONG", "#e74c3c"
    elif avg > 1.0:
        label, color = "USD FIRMING", "#e67e22"
    elif avg < -3.0:
        label, color = "USD WEAK", "#27ae60"
    elif avg < -1.0:
        label, color = "USD SOFTENING", "#2980b9"
    else:
        label, color = "USD STABLE", "#7f8c8d"

    return round(avg, 2), label, color


def _render_dxy_interpretation(dxy_value: float, dxy_label: str, signals: list[FXSignal]) -> None:
    """Write a short plain-language read-through of the DXY proxy into Streamlit."""
    try:
        import streamlit as st
    except ImportError:
        return

    if dxy_label in ("USD STRONG", "USD FIRMING"):
        msg = (
            f"The DXY proxy ({dxy_value:+.1f}% 30d) signals broad USD strength. "
            "Historically, a strong dollar compresses Asian import prices for US buyers, "
            "supporting near-term Trans-Pacific EB demand. However, a persistently strong "
            "dollar can also reduce global trade competitiveness and raise financial stress "
            "in emerging markets — a medium-term headwind for shipping volumes broadly."
        )
    elif dxy_label in ("USD WEAK", "USD SOFTENING"):
        msg = (
            f"The DXY proxy ({dxy_value:+.1f}% 30d) signals broad USD weakness. "
            "A softer dollar typically boosts commodity demand from EM importers and can "
            "accelerate South American and Middle East export volumes. Watch dry bulk "
            "carriers (SBLK) and tanker names for tonne-mile uplift."
        )
    else:
        msg = (
            f"The DXY proxy ({dxy_value:+.1f}% 30d) shows a stable USD environment. "
            "FX is not a primary shipping driver at the moment; focus on supply-side "
            "factors (fleet utilisation, port congestion) for directional signals."
        )

    st.markdown(
        f"<div style='font-size:13px;color:#444;line-height:1.6;'>{msg}</div>",
        unsafe_allow_html=True,
    )


def _format_rate(pair: str, rate: float) -> str:
    """Format exchange rate with appropriate decimal places."""
    if pair in ("USD/KRW", "USD/JPY"):
        return f"{rate:,.1f}"
    return f"{rate:.4f}"
