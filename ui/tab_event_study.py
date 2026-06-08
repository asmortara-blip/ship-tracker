"""tab_event_study.py — the disruption event-study tab.

This is the one analysis on the platform that is **real on both axes**. The
temptation across the rest of the app is to regress the *modeled* disruption
signals (the Shipping Stress Index, congestion scores, voyage-delay estimates)
against equity returns — but those signals are current-date snapshots, not real
historical time series, so any correlation built from them would be an artifact
of the seeding rather than a measured fact. See
:mod:`processing.disruption_event_study` for the full honesty contract.

What *is* real:

  * the **dates** of well-documented past shipping disruptions (Suez 2021, the
    Panama drought 2023, Red Sea / Houthi 2024, …) — :func:`real_event_dates`;
  * real **historical equity prices** for the shipping names, delivered to this
    tab via the ``stock_data`` kwarg the app already fetches once and passes to
    every tab.

So this tab aligns the real prices to the real event dates and *describes* how
prices actually moved in the window around each disruption. That is descriptive
history grounded entirely in real data — **not a forecast, not advice.**

Two views, top to bottom:

  1. **Typical move around a disruption** — :func:`aggregate_event_studies`
     over ALL :func:`real_event_dates`; a sortable table (worst mean abnormal
     return first) plus a horizontal bar of mean abnormal return per ticker.
  2. **Single-event drill-down** — a selectbox of the real events runs
     :func:`event_study` for that one date and tabulates the per-ticker
     cumulative / abnormal / drawdown / run-up moves.

Data flow (all pure below the render wrapper):
  stock_data {ticker: DataFrame}  → close Series per ticker
    → aggregate_event_studies(prices, real_event_dates())   # the typical move
    → event_study(prices, one_date)                         # the drill-down
"""
from __future__ import annotations

import math

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from data.quality import DataSource
from ui.styles import (
    C_ACCENT,
    C_BG,
    C_CONV,
    C_HIGH,
    C_LOW,
    C_MACRO,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    apply_dark_layout,
    badge,
    metric_card_row,
    page_header,
    section_divider,
    source_footer,
    wsj_market_table,
)

# Provenance for the footer. Prices are real market data (scraped via yfinance);
# the event dates are the real/manual historical registry. SCRAPED is the
# honest kind here — real, but not an official first-party feed.
EVENT_STUDY_SOURCE = DataSource.scraped(
    "Event study",
    notes=(
        "Real equity prices (yfinance) aligned to the real dates of past "
        "shipping-disruption events. Descriptive history, not a forecast."
    ),
)

# Trading-day half-widths of the event window. Kept here so the table caption
# and the bar-chart subtitle can quote them honestly.
_PRE = 20
_POST = 20


# ── Pure helpers (no st.* — independently unit-testable) ────────────────────


def _close_series_from_frame(frame) -> pd.Series | None:
    """Extract a clean, date-indexed close-price Series from one ticker frame.

    Defensive by design: ``stock_data`` frames vary in shape across the app's
    feeds. We try the common close-column spellings, fall back to a ``date``
    column for the index when there is no usable DatetimeIndex, and return
    ``None`` (rather than raise) for anything unusable so the caller simply
    skips that ticker. Final cleaning (sort / de-dup / drop non-positive) is
    handled downstream by the module's own coercion.
    """
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return None

    col = None
    for cand in ("close", "Close", "adj_close", "Adj Close", "price"):
        if cand in frame.columns:
            col = cand
            break
    if col is None:
        return None

    selected = frame[col]
    if isinstance(selected, pd.DataFrame):
        # Duplicate column labels or a MultiIndex level-0 match yield a
        # DataFrame, not a Series — take the first column so to_numeric is safe
        # (honours this function's "return None, never raise" contract).
        selected = selected.iloc[:, 0]
    values = pd.to_numeric(selected, errors="coerce")

    # R127: the event study measures cumulative RETURNS over a [-pre,+post]
    # window, so an equity frame's RAW 'close' must ride the look-ahead-free
    # total-return basis (close * adj_factor) — else a split/large dividend
    # inside the window injects a spurious ~50% cumulative move. Only the raw
    # close columns are scaled (adj_close/Adj Close are already adjusted);
    # adj_factor defaults to 1.0 so fixtures/non-equity frames are unchanged.
    if col in ("close", "Close", "price") and "adj_factor" in frame.columns:
        adj = pd.to_numeric(frame["adj_factor"], errors="coerce").fillna(1.0)
        values = values * adj.reindex(values.index).fillna(1.0)

    # Prefer the frame's own index for dates; if it is not datetime-like, look
    # for an explicit 'date' column before giving up.
    index = frame.index
    if not isinstance(index, pd.DatetimeIndex):
        date_col = None
        for cand in ("date", "Date", "datetime", "Datetime"):
            if cand in frame.columns:
                date_col = cand
                break
        if date_col is not None:
            index = pd.to_datetime(frame[date_col], errors="coerce")
        else:
            # Last resort: let the module's coercion try to parse whatever the
            # index is (it handles strings / bad dates → NaT-dropped).
            index = frame.index

    series = pd.Series(values.to_numpy(), index=index)
    series = series.dropna()
    if series.empty:
        return None
    return series


def _prices_by_ticker(stock_data) -> dict[str, pd.Series]:
    """Build ``{ticker: close_series}`` from the ``stock_data`` dict.

    Tickers whose frame yields no usable close series are silently dropped.
    Returns an empty dict for empty / non-dict input — never raises.
    """
    if not isinstance(stock_data, dict) or not stock_data:
        return {}
    out: dict[str, pd.Series] = {}
    for ticker, frame in stock_data.items():
        try:
            series = _close_series_from_frame(frame)
        except Exception:
            # One malformed frame must never take down the whole tab.
            series = None
        if series is not None and len(series) >= 2:
            out[str(ticker)] = series
    return out


def _pct(x: float | None) -> str:
    """Format a fraction as a signed percent, or 'n/a' if non-finite."""
    if x is None or not math.isfinite(float(x)):
        return "n/a"
    return f"{float(x) * 100.0:+.1f}%"


def _annotated_empty(title: str, height: int = 420) -> go.Figure:
    """An annotated-empty figure so the caller can render unconditionally."""
    fig = go.Figure()
    fig.add_annotation(
        text="No event-study data",
        xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False,
        font={"color": C_TEXT3, "size": 12},
    )
    apply_dark_layout(fig, title=title, height=height)
    return fig


def _build_abnormal_bar(aggregate: dict[str, dict]) -> go.Figure:
    """Horizontal bar of mean abnormal return per ticker (worst at the bottom).

    Negative bars are red (``C_LOW``), positive bars green (``C_HIGH``). Only
    tickers with a finite mean abnormal return are plotted. Pure builder —
    empty / all-NaN input returns an annotated-empty figure.
    """
    pairs = [
        (t, float(s.get("mean_abnormal_return", float("nan"))))
        for t, s in (aggregate or {}).items()
        if isinstance(s, dict)
    ]
    pairs = [(t, v) for t, v in pairs if math.isfinite(v)]
    if not pairs:
        return _annotated_empty("Mean abnormal return per ticker")

    # Sort descending so the most negative (worst) abnormal move sits at the
    # TOP of the horizontal bar — Plotly draws the first/highest-value item at
    # the bottom, so the last/worst entry lands at the top (most prominent).
    pairs.sort(key=lambda kv: kv[1], reverse=True)
    tickers = [t for t, _ in pairs]
    vals = [v for _, v in pairs]
    colors = [C_LOW if v < 0 else C_HIGH for v in vals]

    fig = go.Figure(
        go.Bar(
            x=[v * 100.0 for v in vals],
            y=tickers,
            orientation="h",
            marker={"color": colors, "line": {"color": C_BG, "width": 0.8}},
            text=[_pct(v) for v in vals],
            textposition="outside",
            hovertemplate="<b>%{y}</b><br>Mean abnormal: %{x:+.1f}%<extra></extra>",
        )
    )
    apply_dark_layout(
        fig,
        title=(
            f"Mean abnormal return per ticker — "
            f"[-{_PRE}, +{_POST}] trading-day window"
        ),
        height=max(280, 60 + 34 * len(tickers)),
    )
    fig.update_layout(
        margin={"l": 8, "r": 56, "t": 44, "b": 8},
        xaxis={"title": "mean abnormal return (%)", "zeroline": True,
               "zerolinecolor": "rgba(154,150,142,0.4)"},
        yaxis={"title": ""},
    )
    return fig


# ── Render layer (Streamlit) ────────────────────────────────────────────────


def _render_typical_move(prices: dict[str, pd.Series], events: list) -> None:
    """The aggregate 'typical move' section: table + abnormal-return bar."""
    from processing.disruption_event_study import aggregate_event_studies

    aggregate = aggregate_event_studies(prices, [d for _, d in events],
                                         pre=_PRE, post=_POST)
    if not aggregate:
        st.info(
            "No ticker had enough price history aligned to the real event "
            "dates to compute a typical move. (Each ticker needs at least a "
            "couple of pre-event observations around an event.)"
        )
        return

    # Table sorted by mean abnormal return ASCENDING (worst first). NaN abnormal
    # returns sort last so the genuinely-bad names lead.
    def _abn(stats: dict) -> float:
        v = float(stats.get("mean_abnormal_return", float("nan")))
        return v if math.isfinite(v) else float("inf")

    ranked = sorted(aggregate.items(), key=lambda kv: _abn(kv[1]))
    rows: list[list[str]] = []
    for ticker, stats in ranked:
        n_ev = int(stats.get("n_events", 0))
        cum = float(stats.get("mean_cum_return", float("nan")))
        abn = float(stats.get("mean_abnormal_return", float("nan")))
        dd = float(stats.get("mean_max_drawdown", float("nan")))
        cum_color = C_HIGH if (math.isfinite(cum) and cum >= 0) else C_LOW
        abn_color = C_HIGH if (math.isfinite(abn) and abn >= 0) else C_LOW
        rows.append([
            badge(ticker, color=C_TEXT2),
            badge(str(n_ev), color=C_ACCENT),
            badge(_pct(cum), color=cum_color),
            badge(_pct(abn), color=abn_color),
            badge(_pct(dd), color=C_LOW if math.isfinite(dd) else C_TEXT3),
        ])
    wsj_market_table(
        ["Ticker", "# events", "Mean cumulative", "Mean abnormal", "Mean drawdown"],
        rows,
        title=(
            f"Typical move around a disruption — averaged over {len(events)} "
            f"real events ({_PRE}d pre / {_POST}d post window)"
        ),
    )

    st.plotly_chart(
        _build_abnormal_bar(aggregate),
        use_container_width=True,
        config={"displayModeBar": False},
        key="es_abnormal_bar",
    )
    st.caption(
        "Abnormal return = the observed window move minus the drift implied by "
        "each name's own pre-event trend. Negative = the post-event move was "
        "worse than the stock's prior trajectory."
    )


def _render_single_event(prices: dict[str, pd.Series], events: list) -> None:
    """The single-event drill-down: a selectbox of real events → per-ticker table."""
    from processing.disruption_event_study import event_study

    labels = [lbl for lbl, _ in events]
    label_to_date = {lbl: dt for lbl, dt in events}
    pick = st.selectbox(
        "Disruption event",
        options=labels,
        index=0,
        key="es_event_picker",
        help=(
            "Each option is a real, documented past shipping disruption. The "
            "table shows how each name actually moved in the trading-day window "
            "around that date."
        ),
    )
    event_dt = label_to_date.get(pick)
    if event_dt is None:
        st.info("Pick a real disruption event to see its per-ticker move.")
        return

    results = event_study(prices, event_dt, pre=_PRE, post=_POST)
    when = pd.Timestamp(event_dt).strftime("%Y-%m-%d")
    if not results:
        st.info(
            f"No ticker had enough price history around {pick} ({when}) to "
            "study this event."
        )
        return

    # Per-ticker rows, sorted by cumulative window return ascending (worst first).
    ranked = sorted(results.values(), key=lambda r: r.cum_return_window)
    rows: list[list[str]] = []
    for r in ranked:
        cum_color = C_HIGH if r.cum_return_window >= 0 else C_LOW
        abn_color = (
            C_HIGH if (math.isfinite(r.abnormal_return) and r.abnormal_return >= 0)
            else C_LOW
        )
        rows.append([
            badge(r.ticker, color=C_TEXT2),
            badge(_pct(r.cum_return_window), color=cum_color),
            badge(_pct(r.abnormal_return), color=abn_color),
            badge(_pct(r.max_drawdown), color=C_LOW),
            badge(_pct(r.max_runup), color=C_HIGH),
        ])
    wsj_market_table(
        ["Ticker", "Cumulative", "Abnormal", "Max drawdown", "Max run-up"],
        rows,
        title=f"{pick} — {when} · {_PRE}d pre / {_POST}d post window",
    )
    st.caption(
        "All figures are measured from the close on (or just before) the event "
        "date. Descriptive history for this one event — not a forecast."
    )


def render(stock_data=None, **_kwargs) -> None:
    """Render the disruption event-study tab.

    ``stock_data`` is the ``{ticker: DataFrame}`` dict the app fetches once and
    passes to every tab; it carries the REAL equity prices this view needs.
    Every section is wrapped so a failure in one never crashes the page.
    """
    from engine.perf_telemetry import track_render

    with track_render("event_study"):
        from processing.disruption_event_study import (
            NOT_ADVICE,
            aggregate_event_studies,
            real_event_dates,
            summarize,
        )

        page_header(
            title="Disruption Event Study",
            subtitle=(
                "Real equity prices aligned to the real dates of past shipping "
                "disruptions — the one analysis on this platform that is real on "
                "both axes. Descriptive history of how prices actually moved, "
                "not a forecast."
            ),
            badge_text="REAL DATA",
            badge_color=C_MACRO,
        )

        # ── Real events + real prices ──────────────────────────────────────
        try:
            events = real_event_dates()
        except Exception:
            logger.exception("event_study: real_event_dates failed")
            events = []

        prices = _prices_by_ticker(stock_data)

        # Honest empty-data path: this view genuinely needs real equity prices.
        if not prices:
            st.info(
                "Real equity prices aren't loaded right now, so the event study "
                "can't run — this view is deliberately built on real prices "
                "(not modeled signals), aligned to the real dates of past "
                "shipping disruptions. Once the app's equity feed is populated, "
                "this tab will describe how the shipping names actually moved "
                "around each disruption."
            )
            try:
                st.markdown(
                    source_footer([EVENT_STUDY_SOURCE]),
                    unsafe_allow_html=True,
                )
            except Exception:
                logger.exception("event_study: source footer failed (no prices)")
            return

        if not events:
            st.warning(
                "The historical-events registry returned no usable disruption "
                "dates, so there is nothing to align the prices to."
            )
            try:
                st.markdown(
                    source_footer([EVENT_STUDY_SOURCE]),
                    unsafe_allow_html=True,
                )
            except Exception:
                logger.exception("event_study: source footer failed (no events)")
            return

        # ── Honest lede built from the module's own summary string ─────────
        try:
            aggregate_for_lede = aggregate_event_studies(
                prices, [d for _, d in events], pre=_PRE, post=_POST
            )
            lede = summarize(aggregate_for_lede, n_events=len(events))
            st.caption(lede)
        except Exception:
            logger.exception("event_study: lede summary failed")
            # Fall back to the bare honesty disclaimer so the contract still shows.
            st.caption(NOT_ADVICE)

        # ── Coverage KPIs ──────────────────────────────────────────────────
        try:
            metric_card_row([
                {"label": "Real events",
                 "value": str(len(events)),
                 "accent": C_MACRO,
                 "sublabel": "documented past shipping disruptions"},
                {"label": "Tickers with prices",
                 "value": str(len(prices)),
                 "accent": C_ACCENT,
                 "sublabel": "real equity series loaded"},
                {"label": "Event window",
                 "value": f"-{_PRE} / +{_POST}d",
                 "accent": C_MOD,
                 "sublabel": "trading days around each event"},
                {"label": "Basis",
                 "value": "Real × Real",
                 "accent": C_CONV,
                 "sublabel": "real prices × real dates"},
            ], columns=4)
        except Exception:
            logger.exception("event_study: KPI row failed")
            st.error("Coverage summary unavailable.")

        # ── 1. Typical move around a disruption (aggregate) ────────────────
        section_divider("Typical move around a disruption")
        try:
            _render_typical_move(prices, events)
        except Exception:
            logger.exception("event_study: typical-move section failed")
            st.error("Typical-move section unavailable.")

        # ── 2. Single-event drill-down ─────────────────────────────────────
        section_divider("Single-event drill-down")
        try:
            _render_single_event(prices, events)
        except Exception:
            logger.exception("event_study: single-event section failed")
            st.error("Single-event drill-down unavailable.")

        # ── 3. Source footer ───────────────────────────────────────────────
        try:
            st.markdown(
                source_footer([EVENT_STUDY_SOURCE]),
                unsafe_allow_html=True,
            )
        except Exception:
            logger.exception("event_study: source footer failed")
