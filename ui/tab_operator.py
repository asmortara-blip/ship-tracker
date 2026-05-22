"""Operator Dashboard tab — unified admin view across every telemetry layer.

Rolls together the four observability surfaces shipped in recent commits:

  * ``engine.llm_telemetry.get_usage_summary``    — LLM cost / call volume
  * ``engine.alert_analytics.compute_alert_metrics`` — alert ack metrics
  * ``engine.alert_backtest.backtest_alerts``     — alert effectiveness
  * ``engine.perf_telemetry.get_perf_summary``    — tab render performance

Layout
------
1. ``page_header`` masthead with an ADMIN badge.
2. Two 4-up KPI strips (LLM + alerts row, backtest + perf row).
3. Three side-by-side tables: slowest tabs · alerts by severity · LLM by model.
4. Single "System status" line at the bottom — green when healthy, red with a
   warning prefix when CRITICAL is unacked or render success drops below 95%.

Every engine call is wrapped in its own try/except so a single telemetry-layer
failure degrades to ``—`` for that KPI without blanking the rest of the
dashboard. Engine imports are deliberately lazy (inside helpers) so the smoke
harness can import this module even with a broken telemetry stack.
"""
from __future__ import annotations

from typing import Any

import streamlit as st
from loguru import logger

from ui.styles import (
    C_ACCENT,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    wsj_market_table,
)


# ── Cell helpers (kept local — same shape as tab_data_health) ───────────────
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


def _fmt_ms(ms: int | float) -> str:
    """Format a duration; falls back to '—' on non-numeric input."""
    try:
        ms_i = int(ms)
    except (TypeError, ValueError):
        return "—"
    return f"{ms_i / 1000:.2f}s" if ms_i >= 1000 else f"{ms_i:,} ms"


def _format_usd(value: float) -> str:
    """Format a USD value compactly. Lazy-imports utils.helpers and falls
    back to a plain f-string so the dashboard never blanks on an unrelated
    util-package outage."""
    try:
        from utils.helpers import format_usd
        return format_usd(float(value), compact=False)
    except Exception:
        try:
            return f"${float(value):,.2f}"
        except (TypeError, ValueError):
            return "—"


# ── Engine adapters — each returns a dict OR None on failure ────────────────
def _llm_summary(window_days: int) -> dict | None:
    try:
        from engine.llm_telemetry import get_usage_summary
        return get_usage_summary(window_days=window_days)
    except Exception as exc:
        logger.warning(f"Operator dashboard: LLM telemetry failed: {exc}")
        return None


def _alert_metrics(window_days: int):
    try:
        from engine.alert_analytics import compute_alert_metrics
        return compute_alert_metrics(window_days=window_days)
    except Exception as exc:
        logger.warning(f"Operator dashboard: alert analytics failed: {exc}")
        return None


def _alert_backtest(window_days: int, lookback_days: int):
    try:
        from engine.alert_backtest import backtest_alerts
        # We don't have stock_data/freight_data/macro_data on this tab —
        # the backtester tolerates empty dicts (skips alerts it can't score).
        stock_data   = st.session_state.get("stock_data", {}) or {}
        freight_data = st.session_state.get("freight_data", {}) or {}
        macro_data   = st.session_state.get("macro_data", {}) or {}
        return backtest_alerts(
            stock_data, freight_data, macro_data,
            window_days=window_days,
            lookback_days=lookback_days,
        )
    except Exception as exc:
        logger.warning(f"Operator dashboard: alert backtest failed: {exc}")
        return None


def _unack_critical_count(window_days: int) -> int | None:
    try:
        from engine.alert_analytics import get_unacknowledged_critical
        return len(get_unacknowledged_critical(window_days=window_days) or [])
    except Exception as exc:
        logger.warning(f"Operator dashboard: unack critical fetch failed: {exc}")
        return None


def _perf_summary(window_hours: int) -> dict | None:
    try:
        from engine.perf_telemetry import get_perf_summary
        return get_perf_summary(window_hours=window_hours)
    except Exception as exc:
        logger.warning(f"Operator dashboard: perf telemetry failed: {exc}")
        return None


# ── KPI helpers — green/amber/red mapping is intentionally inline ────────────
def _cost_accent(cost: float) -> str:
    return C_HIGH if cost < 5.0 else (C_MOD if cost < 25.0 else C_LOW)


def _ack_accent(rate_pct: float) -> str:
    return C_HIGH if rate_pct >= 80 else (C_MOD if rate_pct >= 50 else C_LOW)


def _success_accent(rate_pct: float) -> str:
    return C_HIGH if rate_pct >= 99 else (C_MOD if rate_pct >= 95 else C_LOW)


def _hit_accent(rate: float) -> str:
    return C_HIGH if rate >= 0.55 else (C_MOD if rate >= 0.45 else C_LOW)


# ── KPI rows ─────────────────────────────────────────────────────────────────
def _render_kpi_strip(
    llm: dict | None,
    metrics: Any,
    backtest: Any,
    unack_crit: int | None,
    perf: dict | None,
    alert_window_d: int,
    llm_window_d: int,
    perf_window_h: int,
) -> None:
    """Render the 2×4 KPI strip — row 1 LLM + alert ack, row 2 effectiveness + perf."""
    # ── Row 1 ─────────────────────────────────────────────────────────────
    llm_calls   = int(llm.get("total_calls", 0))      if llm     else None
    llm_cost    = float(llm.get("total_cost_usd", 0)) if llm     else None
    total_alerts = int(getattr(metrics, "total_alerts", 0)) if metrics else None
    ack_pct = (
        float(getattr(metrics, "ack_rate", 0.0)) * 100.0
        if metrics is not None else None
    )

    metric_card_row([
        {
            "label": "LLM CALLS",
            "value": f"{llm_calls:,}" if llm_calls is not None else "—",
            "accent": C_ACCENT,
            "sublabel": f"last {int(llm_window_d)}d",
        },
        {
            "label": "LLM COST",
            "value": _format_usd(llm_cost) if llm_cost is not None else "—",
            "accent": _cost_accent(llm_cost) if llm_cost is not None else C_TEXT3,
            "sublabel": f"last {int(llm_window_d)}d · list-price est.",
        },
        {
            "label": "ALERTS",
            "value": f"{total_alerts:,}" if total_alerts is not None else "—",
            "accent": C_ACCENT,
            "sublabel": f"last {int(alert_window_d)}d",
        },
        {
            "label": "ACK RATE",
            "value": f"{ack_pct:.1f}%" if ack_pct is not None else "—",
            "accent": _ack_accent(ack_pct) if ack_pct is not None else C_TEXT3,
            "sublabel": f"acknowledged / total ({int(alert_window_d)}d)",
        },
    ], columns=4)

    # ── Row 2 ─────────────────────────────────────────────────────────────
    hit_rate = (
        float(getattr(backtest, "hit_rate", 0.0))
        if backtest is not None else None
    )
    n_eval = int(getattr(backtest, "n_alerts_evaluated", 0)) if backtest else 0
    total_renders = int(perf.get("total_renders", 0)) if perf else None
    sr_pct = float(perf.get("success_rate", 0.0)) * 100.0 if perf else None

    metric_card_row([
        {
            "label": "ALERT HIT RATE",
            "value": (
                f"{hit_rate * 100:.1f}%" if (hit_rate is not None and n_eval > 0)
                else ("0 evaluated" if hit_rate is not None else "—")
            ),
            "accent": _hit_accent(hit_rate) if (hit_rate is not None and n_eval > 0) else C_TEXT3,
            "sublabel": "7d realized · 90d lookback",
        },
        {
            "label": "UNACKED CRITICAL",
            "value": f"{unack_crit}" if unack_crit is not None else "—",
            "accent": (
                C_LOW if (unack_crit or 0) > 0
                else (C_HIGH if unack_crit is not None else C_TEXT3)
            ),
            "sublabel": (
                "needs attention" if (unack_crit or 0) > 0
                else ("all clear" if unack_crit is not None else "telemetry off")
            ),
        },
        {
            "label": "TAB RENDERS",
            "value": f"{total_renders:,}" if total_renders is not None else "—",
            "accent": C_ACCENT,
            "sublabel": f"last {int(perf_window_h)}h",
        },
        {
            "label": "RENDER SUCCESS",
            "value": f"{sr_pct:.2f}%" if sr_pct is not None else "—",
            "accent": _success_accent(sr_pct) if sr_pct is not None else C_TEXT3,
            "sublabel": f"last {int(perf_window_h)}h",
        },
    ], columns=4)


# ── Side-by-side tables ──────────────────────────────────────────────────────
def _render_panel_row(
    perf: dict | None,
    metrics: Any,
    llm: dict | None,
) -> None:
    """Three side-by-side WSJ tables: slowest tabs · alerts by severity · LLM by model."""
    col_l, col_c, col_r = st.columns(3, gap="small")

    with col_l:
        st.markdown(
            f'<div style="font-size:0.72rem;text-transform:uppercase;'
            f'letter-spacing:0.10em;color:{C_TEXT3};font-weight:700;'
            f'margin:6px 0 6px 0">Slowest Tabs</div>',
            unsafe_allow_html=True,
        )
        top_slow = (perf or {}).get("top_slow_tabs", []) or []
        if top_slow:
            headers = ["Tab", "Median", "P95"]
            rows: list[list[str]] = []
            for entry in top_slow[:8]:
                rows.append([
                    _sans(str(entry.get("tab_name", "—")), weight=600),
                    _mono(_fmt_ms(entry.get("median_ms", 0)), color=C_TEXT),
                    _mono(_fmt_ms(entry.get("p95_ms", 0)), color=C_TEXT2),
                ])
            wsj_market_table(headers, rows)
        else:
            st.info("No render data.")

    with col_c:
        st.markdown(
            f'<div style="font-size:0.72rem;text-transform:uppercase;'
            f'letter-spacing:0.10em;color:{C_TEXT3};font-weight:700;'
            f'margin:6px 0 6px 0">Alerts by Severity</div>',
            unsafe_allow_html=True,
        )
        by_sev = (
            getattr(metrics, "by_severity", {}) or {}
            if metrics is not None else {}
        )
        if by_sev:
            headers = ["Sev", "Count", "Ack Rate"]
            rows = []
            sev_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
            for sev_key in sorted(by_sev.keys(), key=lambda s: sev_order.get(s, 99)):
                stats = by_sev[sev_key]
                count = int(stats.get("total", 0))
                rate_pct = float(stats.get("ack_rate", 0.0)) * 100.0
                rate_color = _ack_accent(rate_pct)
                rows.append([
                    _sans(sev_key.title(), weight=700),
                    _mono(f"{count:,}", color=C_TEXT),
                    _mono(f"{rate_pct:.0f}%", color=rate_color, weight=700),
                ])
            wsj_market_table(headers, rows)
        else:
            st.info("No alerts in window.")

    with col_r:
        st.markdown(
            f'<div style="font-size:0.72rem;text-transform:uppercase;'
            f'letter-spacing:0.10em;color:{C_TEXT3};font-weight:700;'
            f'margin:6px 0 6px 0">LLM by Model</div>',
            unsafe_allow_html=True,
        )
        by_model = (llm or {}).get("by_model", {}) or {}
        if by_model:
            headers = ["Model", "Calls", "Cost"]
            rows = []
            # Stable sort: alphabetical by model id, matches tab_data_health.
            for mdl in sorted(by_model.keys()):
                info = by_model[mdl]
                rows.append([
                    _sans(mdl, weight=600),
                    _mono(f"{int(info.get('calls', 0)):,}", color=C_TEXT2),
                    _mono(_format_usd(float(info.get("cost", 0.0))), color=C_TEXT),
                ])
            wsj_market_table(headers, rows)
        else:
            st.info("No LLM calls in window.")


# ── System status footer ─────────────────────────────────────────────────────
def _render_system_status(
    unack_crit: int | None,
    perf: dict | None,
    llm: dict | None,
    llm_window_d: int,
) -> None:
    """One-line system status — green when healthy, red ⚠️-prefixed when not."""
    sr_pct = float(perf.get("success_rate", 0.0)) * 100.0 if perf else None
    unack_n = unack_crit if unack_crit is not None else 0
    cost = float(llm.get("total_cost_usd", 0.0)) if llm else 0.0

    sr_display = f"{sr_pct:.1f}%" if sr_pct is not None else "—"
    unack_display = f"{unack_n}"
    cost_display = _format_usd(cost) if llm is not None else "—"

    bad = (unack_n > 0) or (sr_pct is not None and sr_pct < 95.0)
    if bad:
        prefix = "⚠️ "
        color = C_LOW
        headline = "System attention needed"
    else:
        prefix = ""
        color = C_HIGH
        headline = "System healthy"

    body = (
        f"{unack_display} unacked CRITICAL · render success {sr_display} · "
        f"LLM spend {cost_display} ({int(llm_window_d)}d)"
    )

    st.markdown(
        f'<div style="margin-top:18px;padding:12px 14px;border-radius:4px;'
        f'background:rgba(0,0,0,0.18);border-left:3px solid {color};'
        f'font-family:var(--sans);font-size:0.86rem;color:{color};'
        f'font-weight:600">'
        f'{prefix}<span style="color:{color}">{headline}</span> — '
        f'<span style="color:{C_TEXT};font-weight:500">{body}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ── Main entry point ─────────────────────────────────────────────────────────
def render(*args, **kwargs) -> None:
    """Render the Operator Dashboard — unified telemetry view for admins.

    Accepts ``*args, **kwargs`` for two reasons: (1) the smoke harness in
    ``tests/test_tab_smoke.py`` passes the populated bundle as kwargs, (2)
    keeping the signature open lets ``app.py`` route to this tab without
    needing to plumb any data through — we read what we need from session
    state and the engines themselves.
    """
    from engine.perf_telemetry import track_render

    with track_render("operator"):
        try:
            page_header(
                title="Operator Dashboard",
                subtitle="System health across telemetry layers",
                badge_text="ADMIN",
                badge_color=C_ACCENT,
            )

            # ── Window selectors ──────────────────────────────────────────
            c1, c2 = st.columns(2, gap="small")
            with c1:
                alert_window_d = st.selectbox(
                    "Alert window",
                    options=[7, 30, 90],
                    index=1,  # 30d default
                    format_func=lambda d: f"{d} days",
                    key="operator_alert_window_d",
                )
            with c2:
                llm_window_d = st.selectbox(
                    "LLM window",
                    options=[1, 7],
                    index=1,  # 7d default ("24h" or "7d" per spec)
                    format_func=lambda d: f"{d} day{'s' if d != 1 else ''}",
                    key="operator_llm_window_d",
                )
            # Render-perf window is fixed at 24h per the spec — keeping it
            # off the controls keeps the dashboard a single-glance surface.
            perf_window_h = 24

            # ── Load all telemetry up-front so KPI strip + tables share one
            # snapshot. Each call is its own try/except inside the adapter.
            llm        = _llm_summary(int(llm_window_d))
            metrics    = _alert_metrics(int(alert_window_d))
            # Backtest window kept at the spec defaults (7d realized, 90d
            # lookback) — those are the values the alerts tab uses too.
            backtest   = _alert_backtest(window_days=7, lookback_days=90)
            unack_crit = _unack_critical_count(int(alert_window_d))
            perf       = _perf_summary(int(perf_window_h))

            section_header(
                "Headline KPIs",
                "Eight signals from four telemetry surfaces.",
            )
            _render_kpi_strip(
                llm=llm,
                metrics=metrics,
                backtest=backtest,
                unack_crit=unack_crit,
                perf=perf,
                alert_window_d=int(alert_window_d),
                llm_window_d=int(llm_window_d),
                perf_window_h=int(perf_window_h),
            )

            section_divider("Breakdowns")
            _render_panel_row(perf=perf, metrics=metrics, llm=llm)

            _render_system_status(
                unack_crit=unack_crit,
                perf=perf,
                llm=llm,
                llm_window_d=int(llm_window_d),
            )

        except Exception as exc:
            logger.exception(f"Operator dashboard render error: {exc}")
            st.error(f"Operator dashboard encountered a critical error: {exc}")
