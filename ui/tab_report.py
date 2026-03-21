"""
Investor Intelligence Report Tab — generate and download an institutional-grade
sentiment analysis briefing as a fully self-contained HTML document.

Sections
--------
0. Hero                  — page header + 4 metric boxes
1. Report Configuration  — checkbox panel inside expander
2. Generate & Download   — primary CTA button + download widget
3. Report History        — saved report list with load/delete + stats summary
4. Report Preview        — inline iframe of cover + exec summary
5. Key Insights Preview  — alpha signals, top insights, sentiment bar, macro row
6. News Sentiment Preview — sentiment trend chart, top headlines, topic breakdown

Function signature:
    render(port_results, route_results, insights, freight_data, macro_data, stock_data)
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

# ── Colour palette ─────────────────────────────────────────────────────────────
C_BG      = "#0a0f1a"
C_SURFACE = "#111827"
C_CARD    = "#1a2235"
C_BORDER  = "rgba(255,255,255,0.08)"
C_HIGH    = "#10b981"
C_MOD     = "#f59e0b"
C_LOW     = "#ef4444"
C_ACCENT  = "#3b82f6"
C_CONV    = "#8b5cf6"
C_MACRO   = "#06b6d4"
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"

# ── Imports — best-effort, graceful fallback ───────────────────────────────────

try:
    from ui.styles import (  # noqa: F401 — re-export palette if already imported
        C_BG, C_SURFACE, C_CARD, C_BORDER,
        C_HIGH, C_MOD, C_LOW,
        C_ACCENT, C_CONV, C_MACRO,
        C_TEXT, C_TEXT2, C_TEXT3,
        apply_dark_layout,
    )
    _STYLES_OK = True
except Exception:  # pragma: no cover
    _STYLES_OK = False

    def apply_dark_layout() -> None:  # type: ignore[misc]
        pass


try:
    from processing.investor_report_engine import build_investor_report  # type: ignore[import]
    _ENGINE_OK = True
except Exception:
    _ENGINE_OK = False

    def build_investor_report(*args, **kwargs):  # type: ignore[misc]
        raise ImportError("processing.investor_report_engine not available")


try:
    from utils.investor_report_html import render_investor_report_html  # type: ignore[import]
    _REPORT_HTML_OK = True
except Exception:
    _REPORT_HTML_OK = False

    def render_investor_report_html(report) -> str:  # type: ignore[misc]
        raise ImportError("utils.investor_report_html not available")


# Fallback to the existing html_report module if the investor-specific one is missing
try:
    from utils.html_report import generate_html_report, get_report_as_bytes
    _HTML_REPORT_OK = True
except Exception:
    _HTML_REPORT_OK = False

    def generate_html_report(*args, **kwargs) -> str:  # type: ignore[misc]
        return "<html><body><p>Report unavailable</p></body></html>"

    def get_report_as_bytes(html: str) -> bytes:  # type: ignore[misc]
        return html.encode()


try:
    from utils.report_history import (
        list_reports as _list_reports,
        load_report_html as _load_report_html,
        delete_report as _delete_report,
        get_report_stats as _get_report_stats,
        save_report as _save_report,
    )
    _HISTORY_OK = True
except Exception:
    _HISTORY_OK = False

    def _list_reports():  # type: ignore[misc]
        return []

    def _load_report_html(report_id: str):  # type: ignore[misc]
        return None

    def _delete_report(report_id: str) -> bool:  # type: ignore[misc]
        return False

    def _get_report_stats() -> dict:  # type: ignore[misc]
        return {}

    def _save_report(html_content: str, report_obj) -> None:  # type: ignore[misc]
        return None


try:
    from processing.news_sentiment import (
        fetch_all_news as _fetch_all_news,
        get_sentiment_summary as _get_sentiment_summary,
    )
    _NEWS_OK = True
except Exception:
    _NEWS_OK = False

    def _fetch_all_news(cache=None, ttl_hours=2.0):  # type: ignore[misc]
        return []

    def _get_sentiment_summary(articles):  # type: ignore[misc]
        return {}


logger = logging.getLogger(__name__)


# ── Internal helpers ───────────────────────────────────────────────────────────

def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _score_color(score: float) -> str:
    if score >= 0.65:
        return C_HIGH
    if score >= 0.45:
        return C_MOD
    return C_LOW


def _risk_color(level: str) -> str:
    mapping = {
        "LOW":      C_HIGH,
        "MODERATE": C_MOD,
        "HIGH":     C_LOW,
        "CRITICAL": "#b91c1c",
    }
    return mapping.get(level.upper(), C_MOD)


def _action_color(action: str) -> str:
    mapping = {
        "Prioritize": C_HIGH,
        "Monitor":    C_ACCENT,
        "Watch":      C_TEXT2,
        "Caution":    C_MOD,
        "Avoid":      C_LOW,
    }
    return mapping.get(action, C_TEXT2)


def _dark_layout(height: int = 400, margin: dict | None = None) -> dict:
    m = margin or dict(l=20, r=20, t=36, b=20)
    return dict(
        paper_bgcolor=C_BG,
        plot_bgcolor=C_SURFACE,
        font=dict(color=C_TEXT, family="Inter, sans-serif", size=11),
        height=height,
        margin=m,
        hoverlabel=dict(
            bgcolor=C_CARD,
            bordercolor="rgba(255,255,255,0.15)",
            font=dict(color=C_TEXT, size=12),
        ),
    )


def _axis_style() -> dict:
    return dict(
        gridcolor="rgba(255,255,255,0.04)",
        zerolinecolor="rgba(255,255,255,0.08)",
        tickfont=dict(color=C_TEXT3, size=10),
        linecolor="rgba(255,255,255,0.08)",
    )


def _badge(text: str, color: str) -> str:
    return (
        f'<span style="background:{_hex_to_rgba(color, 0.15)};color:{color};'
        f'border:1px solid {_hex_to_rgba(color, 0.35)};'
        f'padding:3px 12px;border-radius:999px;font-size:0.72rem;font-weight:700;'
        f'white-space:nowrap">{text}</span>'
    )


def _stat_card(label: str, value: str, sub: str = "", color: str = C_ACCENT,
               glow: bool = False) -> str:
    sub_html = (
        f'<div style="font-size:0.7rem;color:{C_TEXT3};margin-top:4px">{sub}</div>'
        if sub else ""
    )
    shadow = f"box-shadow:0 0 24px {_hex_to_rgba(color, 0.20)};" if glow else ""
    return (
        f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
        f'border-top:2px solid {color};border-radius:12px;padding:16px 18px;'
        f'text-align:center;{shadow}">'
        f'<div style="font-size:0.62rem;font-weight:700;color:{C_TEXT3};'
        f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px">{label}</div>'
        f'<div style="font-size:1.4rem;font-weight:800;color:{C_TEXT};line-height:1">{value}</div>'
        f'{sub_html}'
        f'</div>'
    )


def _divider(label: str) -> None:
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:12px;margin:32px 0 20px">'
        f'<div style="flex:1;height:1px;background:linear-gradient(90deg,'
        f'rgba(255,255,255,0.0),rgba(255,255,255,0.08))"></div>'
        f'<span style="font-size:0.62rem;color:{C_TEXT3};text-transform:uppercase;'
        f'letter-spacing:0.14em;font-weight:700">{label}</span>'
        f'<div style="flex:1;height:1px;background:linear-gradient(90deg,'
        f'rgba(255,255,255,0.08),rgba(255,255,255,0.0))"></div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _safe_get(obj: Any, *keys, default: Any = None) -> Any:
    """Safely get a field from a dict or attribute-based object."""
    for key in keys:
        if obj is None:
            return default
        if isinstance(obj, dict):
            obj = obj.get(key, None)
        else:
            obj = getattr(obj, key, None)
    return obj if obj is not None else default


# ── Compute summary metrics from insights ─────────────────────────────────────

def _compute_metrics(insights: list) -> dict:
    """Derive the 4 hero metric values from the insights list."""
    if not insights:
        return {
            "sentiment_label": "NEUTRAL",
            "sentiment_score": 0.0,
            "alpha_count":     0,
            "risk_level":      "MODERATE",
            "data_quality":    "Limited",
        }
    scores = [_safe_get(i, "score", default=0.0) for i in insights]
    avg = sum(scores) / len(scores) if scores else 0.0
    if avg >= 0.65:
        sentiment_label = "BULLISH"
    elif avg >= 0.45:
        sentiment_label = "NEUTRAL"
    else:
        sentiment_label = "BEARISH"

    # Count alpha signals: insights with score >= 0.60 and action == Prioritize / Monitor
    alpha_count = sum(
        1 for i in insights
        if _safe_get(i, "score", default=0.0) >= 0.60
        and _safe_get(i, "action", default="") in ("Prioritize", "Monitor")
    )

    # Risk level
    bearish_count = sum(1 for i in insights if _safe_get(i, "score", default=0.5) < 0.40)
    if avg >= 0.70 and bearish_count == 0:
        risk_level = "LOW"
    elif avg >= 0.50:
        risk_level = "MODERATE"
    elif bearish_count >= len(insights) // 2:
        risk_level = "HIGH"
    else:
        risk_level = "MODERATE"

    # Data quality by coverage
    n = len(insights)
    if n >= 15:
        quality = "Excellent"
    elif n >= 8:
        quality = "Good"
    elif n >= 3:
        quality = "Fair"
    else:
        quality = "Limited"

    return {
        "sentiment_label": sentiment_label,
        "sentiment_score": avg,
        "alpha_count":     alpha_count,
        "risk_level":      risk_level,
        "data_quality":    quality,
    }


# ── Section 0: Hero ───────────────────────────────────────────────────────────

def _render_hero(insights: list) -> None:
    try:
        metrics = _compute_metrics(insights)
        sentiment_label = metrics["sentiment_label"]
        sentiment_score = metrics["sentiment_score"]
        alpha_count     = metrics["alpha_count"]
        risk_level      = metrics["risk_level"]
        data_quality    = metrics["data_quality"]

        s_color = C_HIGH if sentiment_label == "BULLISH" else (C_LOW if sentiment_label == "BEARISH" else C_MOD)
        r_color = _risk_color(risk_level)
        q_color = C_HIGH if data_quality in ("Excellent", "Good") else (C_MOD if data_quality == "Fair" else C_LOW)

        st.markdown(
            f"""
            <div style="background:linear-gradient(135deg,#0d1b2e 0%,#1a2a45 60%,#0f1f35 100%);
                        border:1px solid {C_ACCENT}33;border-radius:16px;
                        padding:28px 32px 24px;margin-bottom:20px;
                        box-shadow:0 8px 40px rgba(59,130,246,0.12);">
              <div style="font-size:0.72rem;font-weight:700;letter-spacing:0.12em;
                          color:{C_ACCENT};text-transform:uppercase;margin-bottom:6px;">
                INVESTOR INTELLIGENCE REPORT
              </div>
              <div style="font-size:1.75rem;font-weight:800;color:{C_TEXT};
                          letter-spacing:-0.02em;line-height:1.2;margin-bottom:6px;">
                Institutional-grade sentiment analysis,&nbsp;
                <span style="color:{C_CONV};">alpha signals</span>,
                and market intelligence
              </div>
              <div style="font-size:0.8rem;color:{C_TEXT3};margin-bottom:0;">
                Compiled into a downloadable briefing &nbsp;·&nbsp;
                {datetime.now(tz=timezone.utc).strftime("%d %b %Y  %H:%M UTC")}
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        cols = st.columns(4)
        cards = [
            _stat_card(
                "Market Sentiment",
                sentiment_label,
                sub=f"Score {sentiment_score:.2f}",
                color=s_color,
                glow=True,
            ),
            _stat_card(
                "Active Alpha Signals",
                str(alpha_count),
                sub="Score ≥ 0.60",
                color=C_CONV,
            ),
            _stat_card(
                "Risk Level",
                risk_level,
                color=r_color,
            ),
            _stat_card(
                "Data Quality",
                data_quality,
                sub=f"{len(insights)} insights indexed",
                color=q_color,
            ),
        ]
        for col, card in zip(cols, cards):
            with col:
                st.markdown(card, unsafe_allow_html=True)

    except Exception:
        logger.exception("tab_report: hero section failed")
        st.warning("Hero section unavailable.")


# ── Section 1: Report Configuration ──────────────────────────────────────────

def _render_config() -> dict:
    """Render the configuration expander and return the checkbox state dict."""
    cfg: dict = {}
    try:
        with st.expander("⚙️ Report Configuration", expanded=False):
            col_a, col_b = st.columns(2)
            with col_a:
                cfg["include_sentiment"]       = st.checkbox("Market Sentiment Analysis",        value=True,  key="rep_cfg_sentiment")
                cfg["include_alpha"]           = st.checkbox("Alpha Signal Intelligence",         value=True,  key="rep_cfg_alpha")
                cfg["include_market"]          = st.checkbox("Market Intelligence & Insights",    value=True,  key="rep_cfg_market")
                cfg["include_recommendations"] = st.checkbox("AI Recommendations",               value=True,  key="rep_cfg_recs")
            with col_b:
                cfg["include_freight"]         = st.checkbox("Freight Rate Analysis",            value=True,  key="rep_cfg_freight")
                cfg["include_macro"]           = st.checkbox("Macro Environment",                value=True,  key="rep_cfg_macro")
                cfg["include_stocks"]          = st.checkbox("Shipping Stock Analysis",          value=True,  key="rep_cfg_stocks")
    except Exception:
        logger.exception("tab_report: config section failed")
        st.warning("Configuration panel unavailable.")
    return cfg


# ── Section 2: Generate & Download ───────────────────────────────────────────

def _render_generate(
    port_results, route_results, insights, freight_data, macro_data, stock_data
) -> None:
    try:
        _divider("Generate Report")

        generate_clicked = st.button(
            "Generate Report",
            type="primary",
            use_container_width=True,
            key="rep_generate_btn",
        )

        if generate_clicked:
            with st.spinner("Compiling market intelligence..."):
                try:
                    if _ENGINE_OK and _REPORT_HTML_OK:
                        report = build_investor_report(
                            port_results, route_results, insights,
                            freight_data, macro_data, stock_data,
                        )
                        html_str = render_investor_report_html(report)
                    else:
                        # Graceful fallback to the existing HTML report generator
                        html_str = generate_html_report(
                            port_results, route_results, insights,
                            freight_data, macro_data, stock_data,
                        )
                        report = None

                    html_bytes = get_report_as_bytes(html_str) if _HTML_REPORT_OK else html_str.encode()
                    st.session_state["report_html"]  = html_str
                    st.session_state["report_bytes"] = html_bytes
                    st.session_state["report_obj"]   = report

                    # Persist to report history — non-blocking
                    if _HISTORY_OK and html_str and report is not None:
                        try:
                            _save_report(html_content=html_str, report_obj=report)
                        except Exception as _save_exc:
                            logger.warning("tab_report: save_report failed (non-fatal): %s", _save_exc)

                    # Success summary
                    n_insights = len(insights) if insights else 0
                    n_routes   = len(route_results) if route_results else 0
                    n_ports    = len(port_results) if port_results else 0
                    st.success(
                        f"Report compiled — {n_insights} insights · "
                        f"{n_routes} routes · {n_ports} ports"
                    )

                except Exception as e:
                    st.error("Report generation failed. Please try again.")
                    st.exception(e)

        # Download button — always shown after first generation
        html_bytes = st.session_state.get("report_bytes")
        if html_bytes:
            date_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
            st.download_button(
                label="📥 Download Report (HTML)",
                data=html_bytes,
                file_name=f"shipping_intelligence_report_{date_str}.html",
                mime="text/html",
                use_container_width=True,
                key="rep_download_btn",
            )

    except Exception:
        logger.exception("tab_report: generate section failed")
        st.warning("Generate section unavailable.")


# ── Section 3: Report Preview ─────────────────────────────────────────────────

def _render_preview() -> None:
    try:
        html_str: str | None = st.session_state.get("report_html")
        if not html_str:
            return

        _divider("Report Preview")
        st.caption("Full report opens in your browser after download. Showing cover + executive summary preview.")

        # Truncate to roughly the first two sections to avoid overwhelming the page.
        # Strategy: keep up to the first occurrence of the 3rd <section> tag, or 12 000 chars.
        truncated = html_str
        try:
            # Find position of third <section (or <div class="section")
            idx = 0
            for _ in range(2):
                next_idx = truncated.find("<section", idx + 1)
                if next_idx == -1:
                    break
                idx = next_idx
            # Close off the HTML cleanly
            cutoff = min(idx if idx > 0 else len(truncated), 14_000)
            truncated = html_str[:cutoff] + "\n</body></html>"
        except Exception:
            truncated = html_str[:14_000] + "\n</body></html>"

        components.html(truncated, height=800, scrolling=True)

    except Exception:
        logger.exception("tab_report: preview section failed")
        st.warning("Report preview unavailable.")


# ── Section 4: Key Insights Preview ──────────────────────────────────────────

def _render_key_insights(insights: list, macro_data: dict | None, stock_data: dict | None) -> None:
    try:
        _divider("Report Snapshot")
        st.subheader("📊 Report Snapshot")

        if not insights:
            st.info("No insights available yet. Run the analysis engine to populate this section.")
            return

        sorted_insights = sorted(insights, key=lambda i: _safe_get(i, "score", default=0.0), reverse=True)

        # ── Alpha signals (top 3 high-conviction insights) ────────────────────
        alpha_signals = [
            i for i in sorted_insights
            if _safe_get(i, "score", default=0.0) >= 0.60
            and _safe_get(i, "action", default="") in ("Prioritize", "Monitor")
        ][:3]

        if alpha_signals:
            st.markdown(
                f'<div style="font-size:0.8rem;font-weight:700;color:{C_CONV};'
                f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:8px">'
                f'Top Alpha Signals</div>',
                unsafe_allow_html=True,
            )
            sig_cols = st.columns(min(len(alpha_signals), 3))
            for col, sig in zip(sig_cols, alpha_signals):
                ticker    = _safe_get(sig, "category", default="—")
                action    = _safe_get(sig, "action",   default="—")
                score     = _safe_get(sig, "score",    default=0.0)
                title     = _safe_get(sig, "title",    default="—")
                s_color   = _action_color(action)
                with col:
                    st.markdown(
                        f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
                        f'border-left:3px solid {s_color};border-radius:10px;'
                        f'padding:14px 16px;margin-bottom:8px;">'
                        f'<div style="font-size:0.65rem;font-weight:700;color:{C_TEXT3};'
                        f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px">{ticker}</div>'
                        f'<div style="font-size:0.85rem;font-weight:700;color:{C_TEXT};'
                        f'margin-bottom:8px;line-height:1.3">{title}</div>'
                        f'<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">'
                        f'{_badge(action, s_color)}'
                        f'<span style="font-size:0.72rem;color:{C_TEXT3}">Score {score:.0%}</span>'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

        # ── Top 3 insights as compact cards ──────────────────────────────────
        st.markdown(
            f'<div style="font-size:0.8rem;font-weight:700;color:{C_ACCENT};'
            f'text-transform:uppercase;letter-spacing:0.1em;margin:16px 0 8px">'
            f'Top Insights</div>',
            unsafe_allow_html=True,
        )
        top_insights = sorted_insights[:3]
        for insight in top_insights:
            title  = _safe_get(insight, "title",  default="Untitled")
            action = _safe_get(insight, "action", default="—")
            score  = _safe_get(insight, "score",  default=0.0)
            cat    = _safe_get(insight, "category", default="")
            i_color = _score_color(score)
            a_color = _action_color(action)
            st.markdown(
                f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
                f'border-radius:8px;padding:10px 14px;margin-bottom:6px;'
                f'display:flex;align-items:center;justify-content:space-between;gap:12px">'
                f'<div style="flex:1;min-width:0">'
                f'<span style="font-size:0.82rem;font-weight:600;color:{C_TEXT}">{title}</span>'
                f'&nbsp;<span style="font-size:0.65rem;color:{C_TEXT3}">{cat}</span>'
                f'</div>'
                f'<div style="display:flex;gap:8px;align-items:center;flex-shrink:0">'
                f'{_badge(action, a_color)}'
                f'<span style="font-size:0.75rem;font-weight:700;color:{i_color}">{score:.0%}</span>'
                f'</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        # ── Sentiment breakdown bar ───────────────────────────────────────────
        scores_list = [_safe_get(i, "score", default=0.0) for i in insights]
        bullish_pct = sum(1 for s in scores_list if s >= 0.65) / len(scores_list) * 100 if scores_list else 0
        neutral_pct = sum(1 for s in scores_list if 0.45 <= s < 0.65) / len(scores_list) * 100 if scores_list else 0
        bearish_pct = 100 - bullish_pct - neutral_pct

        st.markdown(
            f'<div style="margin:20px 0 6px">'
            f'<div style="font-size:0.72rem;font-weight:700;color:{C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.1em;margin-bottom:8px">'
            f'Sentiment Distribution</div>'
            f'<div style="display:flex;border-radius:6px;overflow:hidden;height:18px;gap:2px">'
            f'<div style="width:{bullish_pct:.1f}%;background:{C_HIGH};'
            f'display:flex;align-items:center;justify-content:center;'
            f'font-size:0.6rem;font-weight:700;color:#fff;min-width:0;">'
            f'{"Bullish" if bullish_pct > 15 else ""}</div>'
            f'<div style="width:{neutral_pct:.1f}%;background:{C_MOD};'
            f'display:flex;align-items:center;justify-content:center;'
            f'font-size:0.6rem;font-weight:700;color:#fff;min-width:0;">'
            f'{"Neutral" if neutral_pct > 15 else ""}</div>'
            f'<div style="width:{bearish_pct:.1f}%;background:{C_LOW};'
            f'display:flex;align-items:center;justify-content:center;'
            f'font-size:0.6rem;font-weight:700;color:#fff;min-width:0;">'
            f'{"Bearish" if bearish_pct > 15 else ""}</div>'
            f'</div>'
            f'<div style="display:flex;gap:16px;margin-top:6px">'
            f'<span style="font-size:0.65rem;color:{C_HIGH}">■ Bullish {bullish_pct:.0f}%</span>'
            f'<span style="font-size:0.65rem;color:{C_MOD}">■ Neutral {neutral_pct:.0f}%</span>'
            f'<span style="font-size:0.65rem;color:{C_LOW}">■ Bearish {bearish_pct:.0f}%</span>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # ── Key macro values ──────────────────────────────────────────────────
        if macro_data and isinstance(macro_data, dict):
            macro_items = list(macro_data.items())[:6]
            if macro_items:
                st.markdown(
                    f'<div style="font-size:0.72rem;font-weight:700;color:{C_MACRO};'
                    f'text-transform:uppercase;letter-spacing:0.1em;margin:16px 0 8px">'
                    f'Key Macro Indicators</div>',
                    unsafe_allow_html=True,
                )
                macro_cols = st.columns(min(len(macro_items), 6))
                for col, (k, v) in zip(macro_cols, macro_items):
                    val_str = f"{v:.2f}" if isinstance(v, (int, float)) else str(v)[:12]
                    with col:
                        st.markdown(
                            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
                            f'border-top:2px solid {C_MACRO};border-radius:8px;'
                            f'padding:10px 12px;text-align:center;">'
                            f'<div style="font-size:0.58rem;color:{C_TEXT3};'
                            f'text-transform:uppercase;margin-bottom:4px">{k}</div>'
                            f'<div style="font-size:1rem;font-weight:800;color:{C_TEXT}">{val_str}</div>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )

    except Exception:
        logger.exception("tab_report: key insights preview failed")
        st.warning("Key insights preview unavailable.")


# ── Section 5: News Sentiment Preview ────────────────────────────────────────

def _render_news_preview() -> None:
    try:
        _divider("News Sentiment Preview")

        if not _NEWS_OK:
            st.info("News sentiment module not available.")
            return

        with st.spinner("Fetching shipping news..."):
            try:
                articles_raw = _fetch_all_news(cache=None, ttl_hours=2.0)
            except Exception:
                articles_raw = []

        # Normalise to plain dicts
        articles: list[dict] = []
        for a in articles_raw:
            if isinstance(a, dict):
                articles.append(a)
            else:
                articles.append({
                    "title":           getattr(a, "title",           "Untitled"),
                    "source":          getattr(a, "source",          "Unknown"),
                    "published_dt":    getattr(a, "published_dt",    datetime.now(tz=timezone.utc)),
                    "sentiment_score": getattr(a, "sentiment_score", 0.0) or 0.0,
                    "sentiment_label": getattr(a, "sentiment_label", "NEUTRAL") or "NEUTRAL",
                    "topic_tags":      getattr(a, "topic_tags",      []) or [],
                    "url":             getattr(a, "url",             "#"),
                    "summary":         getattr(a, "summary",         ""),
                })

        if not articles:
            st.info("No news articles available at this time.")
            return

        # ── Sentiment trend chart (last 30 days) ──────────────────────────────
        now_utc = datetime.now(tz=timezone.utc)
        cutoff  = now_utc - timedelta(days=30)

        dated_articles = []
        for a in articles:
            pub = a.get("published_dt", now_utc)
            if pub is not None and hasattr(pub, "tzinfo"):
                if pub.tzinfo is None:
                    pub = pub.replace(tzinfo=timezone.utc)
                if pub >= cutoff:
                    dated_articles.append((pub.date(), a.get("sentiment_score", 0.0) or 0.0))

        if len(dated_articles) >= 3:
            try:
                by_date: dict = defaultdict(list)
                for date_val, score in dated_articles:
                    by_date[date_val].append(score)
                sorted_dates = sorted(by_date.keys())
                avg_scores   = [sum(by_date[d]) / len(by_date[d]) for d in sorted_dates]

                fig_trend = go.Figure()
                fig_trend.add_trace(go.Scatter(
                    x=sorted_dates,
                    y=avg_scores,
                    mode="lines+markers",
                    name="Avg Sentiment",
                    line=dict(color=C_ACCENT, width=2),
                    marker=dict(
                        color=[C_HIGH if s > 0.05 else (C_LOW if s < -0.05 else C_MOD) for s in avg_scores],
                        size=7,
                    ),
                    hovertemplate="%{x}<br>Sentiment: %{y:.3f}<extra></extra>",
                ))
                fig_trend.add_hline(y=0, line=dict(color="rgba(255,255,255,0.15)", dash="dash", width=1))
                layout = _dark_layout(height=280)
                layout.update(
                    title=dict(text="30-Day Sentiment Trend", font=dict(size=13, color=C_TEXT), x=0.01),
                    xaxis=dict(**_axis_style()),
                    yaxis=dict(**_axis_style(), title=dict(text="Sentiment", font=dict(color=C_TEXT3, size=10))),
                    showlegend=False,
                )
                fig_trend.update_layout(**layout)
                st.plotly_chart(fig_trend, use_container_width=True, key="rep_news_trend_chart")
            except Exception:
                logger.exception("tab_report: sentiment trend chart failed")
                st.caption("Sentiment trend chart unavailable.")

        # ── Top 5 headlines ───────────────────────────────────────────────────
        def _sent_color(label: str) -> str:
            if label == "BULLISH":
                return C_HIGH
            if label == "BEARISH":
                return C_LOW
            return C_TEXT3

        st.markdown(
            f'<div style="font-size:0.8rem;font-weight:700;color:{C_TEXT};'
            f'text-transform:uppercase;letter-spacing:0.1em;margin:16px 0 8px">'
            f'Top Headlines</div>',
            unsafe_allow_html=True,
        )
        top5 = sorted(articles, key=lambda a: abs(a.get("sentiment_score", 0.0) or 0.0), reverse=True)[:5]
        for article in top5:
            label  = article.get("sentiment_label", "NEUTRAL") or "NEUTRAL"
            score  = article.get("sentiment_score", 0.0) or 0.0
            title  = article.get("title", "—")
            source = article.get("source", "—")
            url    = article.get("url", "#")
            lc     = _sent_color(label)
            st.markdown(
                f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
                f'border-left:3px solid {lc};border-radius:8px;'
                f'padding:10px 14px;margin-bottom:6px;display:flex;'
                f'align-items:center;gap:12px;">'
                f'{_badge(label, lc)}'
                f'<div style="flex:1;min-width:0">'
                f'<a href="{url}" target="_blank" style="font-size:0.82rem;font-weight:600;'
                f'color:{C_TEXT};text-decoration:none;line-height:1.3">{title}</a>'
                f'<div style="font-size:0.65rem;color:{C_TEXT3};margin-top:3px">{source}</div>'
                f'</div>'
                f'<span style="font-size:0.75rem;font-weight:700;color:{lc};flex-shrink:0">'
                f'{score:+.2f}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

        # ── Topic breakdown horizontal bar ────────────────────────────────────
        topic_counts: dict[str, int] = defaultdict(int)
        for a in articles:
            for tag in (a.get("topic_tags") or []):
                topic_counts[tag] += 1

        if topic_counts:
            try:
                topics_sorted = sorted(topic_counts.items(), key=lambda x: x[1], reverse=True)
                t_labels = [t for t, _ in topics_sorted]
                t_vals   = [c for _, c in topics_sorted]
                _TOPIC_COLORS_MAP = {
                    "rates":      C_ACCENT,
                    "congestion": C_MOD,
                    "sanctions":  C_LOW,
                    "weather":    C_MACRO,
                    "M&A":        C_CONV,
                    "regulatory": "#ec4899",
                }
                bar_colors = [_TOPIC_COLORS_MAP.get(t, C_TEXT3) for t in t_labels]

                fig_topic = go.Figure(go.Bar(
                    x=t_vals,
                    y=t_labels,
                    orientation="h",
                    marker=dict(color=bar_colors),
                    hovertemplate="%{y}: %{x} articles<extra></extra>",
                ))
                layout_topic = _dark_layout(height=240)
                layout_topic.update(
                    title=dict(text="News by Topic", font=dict(size=13, color=C_TEXT), x=0.01),
                    xaxis=dict(**_axis_style(), title=dict(text="Article Count", font=dict(color=C_TEXT3, size=10))),
                    yaxis=dict(**_axis_style()),
                    showlegend=False,
                )
                fig_topic.update_layout(**layout_topic)
                st.plotly_chart(fig_topic, use_container_width=True, key="rep_news_topic_chart")
            except Exception:
                logger.exception("tab_report: topic chart failed")
                st.caption("Topic breakdown chart unavailable.")

    except Exception:
        logger.exception("tab_report: news preview section failed")
        st.warning("News sentiment preview unavailable.")


# ── Section 6: Report History ─────────────────────────────────────────────────

def _sentiment_badge_color(label: str) -> str:
    mapping = {
        "BULLISH": C_HIGH,
        "BEARISH": C_LOW,
        "NEUTRAL": C_MOD,
        "MIXED":   C_ACCENT,
    }
    return mapping.get((label or "NEUTRAL").upper(), C_TEXT3)


def _render_report_history() -> None:
    try:
        _divider("Report History")
        st.subheader("📁 Report History")

        if not _HISTORY_OK:
            st.info("Report history module not available.")
            return

        # ── Summary stats ─────────────────────────────────────────────────────
        try:
            stats = _get_report_stats()
            total = stats.get("total_reports", 0)
            if total > 0:
                size_mb   = stats.get("total_size_mb", 0.0)
                avg_score = stats.get("avg_sentiment_score", 0.0)
                dist      = stats.get("sentiment_distribution", {})
                dominant  = max(dist, key=dist.get) if dist else "—"
                d_color   = _sentiment_badge_color(dominant)

                st.markdown(
                    f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
                    f'border-radius:10px;padding:14px 18px;margin-bottom:16px;'
                    f'display:flex;gap:28px;align-items:center;flex-wrap:wrap;">'
                    f'<div style="text-align:center">'
                    f'<div style="font-size:0.6rem;color:{C_TEXT3};text-transform:uppercase;'
                    f'letter-spacing:0.1em;margin-bottom:2px">Total Reports</div>'
                    f'<div style="font-size:1.3rem;font-weight:800;color:{C_TEXT}">{total}</div>'
                    f'</div>'
                    f'<div style="text-align:center">'
                    f'<div style="font-size:0.6rem;color:{C_TEXT3};text-transform:uppercase;'
                    f'letter-spacing:0.1em;margin-bottom:2px">Total Size</div>'
                    f'<div style="font-size:1.3rem;font-weight:800;color:{C_TEXT}">{size_mb:.2f} MB</div>'
                    f'</div>'
                    f'<div style="text-align:center">'
                    f'<div style="font-size:0.6rem;color:{C_TEXT3};text-transform:uppercase;'
                    f'letter-spacing:0.1em;margin-bottom:2px">Avg Sentiment</div>'
                    f'<div style="font-size:1.3rem;font-weight:800;color:{C_TEXT}">{avg_score:+.3f}</div>'
                    f'</div>'
                    f'<div style="text-align:center">'
                    f'<div style="font-size:0.6rem;color:{C_TEXT3};text-transform:uppercase;'
                    f'letter-spacing:0.1em;margin-bottom:2px">Dominant Sentiment</div>'
                    f'<div style="margin-top:4px">{_badge(dominant, d_color)}</div>'
                    f'</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        except Exception:
            logger.exception("tab_report: report history stats failed")

        # ── Report list ───────────────────────────────────────────────────────
        try:
            reports = _list_reports()
        except Exception:
            logger.exception("tab_report: list_reports failed")
            reports = []

        if not reports:
            st.info("No saved reports yet. Generate your first report above.")
            return

        for idx, meta in enumerate(reports):
            try:
                sent_label  = (meta.sentiment_label or "NEUTRAL").upper()
                risk_label  = (meta.risk_level or "MODERATE").upper()
                dq_label    = (meta.data_quality or "PARTIAL").upper()
                s_color     = _sentiment_badge_color(sent_label)
                r_color     = _risk_color(risk_label)
                dq_color    = C_HIGH if dq_label == "FULL" else (C_MOD if dq_label == "PARTIAL" else C_LOW)
                size_str    = f"{meta.file_size_kb:.1f} KB"
                sig_str     = str(meta.signal_count)
                report_date = meta.report_date or meta.generated_at[:10]

                # Header row
                col_info, col_load, col_del = st.columns([6, 1, 1])

                with col_info:
                    st.markdown(
                        f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
                        f'border-left:3px solid {s_color};border-radius:10px;'
                        f'padding:12px 16px;display:flex;align-items:center;'
                        f'gap:14px;flex-wrap:wrap;">'
                        f'<div style="min-width:90px">'
                        f'<div style="font-size:0.65rem;color:{C_TEXT3};margin-bottom:2px">Date</div>'
                        f'<div style="font-size:0.85rem;font-weight:700;color:{C_TEXT}">{report_date}</div>'
                        f'</div>'
                        f'<div>{_badge(sent_label, s_color)}</div>'
                        f'<div>'
                        f'<span style="font-size:0.65rem;color:{C_TEXT3}">Risk: </span>'
                        f'{_badge(risk_label, r_color)}'
                        f'</div>'
                        f'<div>'
                        f'<span style="font-size:0.65rem;color:{C_TEXT3}">Signals: </span>'
                        f'<span style="font-size:0.8rem;font-weight:700;color:{C_CONV}">{sig_str}</span>'
                        f'</div>'
                        f'<div>'
                        f'<span style="font-size:0.65rem;color:{C_TEXT3}">Quality: </span>'
                        f'{_badge(dq_label, dq_color)}'
                        f'</div>'
                        f'<div style="margin-left:auto">'
                        f'<span style="font-size:0.65rem;color:{C_TEXT3}">{size_str}</span>'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                with col_load:
                    if st.button(
                        "Load",
                        key=f"rep_hist_load_{meta.report_id}_{idx}",
                        use_container_width=True,
                        help="Load this report into the preview panel",
                    ):
                        try:
                            html = _load_report_html(meta.report_id)
                            if html:
                                st.session_state["report_html"]  = html
                                st.session_state["report_bytes"] = html.encode()
                                st.session_state["report_obj"]   = None
                                st.success(f"Loaded report from {report_date}.")
                                st.rerun()
                            else:
                                st.warning("Report file not found on disk.")
                        except Exception:
                            logger.exception("tab_report: load report failed")
                            st.warning("Could not load report.")

                with col_del:
                    if st.button(
                        "Delete",
                        key=f"rep_hist_del_{meta.report_id}_{idx}",
                        use_container_width=True,
                        help="Permanently delete this report",
                        type="secondary",
                    ):
                        try:
                            _delete_report(meta.report_id)
                            st.rerun()
                        except Exception:
                            logger.exception("tab_report: delete report failed")
                            st.warning("Could not delete report.")

            except Exception:
                logger.exception("tab_report: error rendering history row %d", idx)
                st.warning(f"Could not render report entry {idx + 1}.")

    except Exception:
        logger.exception("tab_report: report history section failed")
        st.warning("Report history unavailable.")


# ── Public entry point ────────────────────────────────────────────────────────

def render(
    port_results,
    route_results,
    insights,
    freight_data,
    macro_data,
    stock_data,
) -> None:
    """Render the Investor Intelligence Report tab.

    Parameters
    ----------
    port_results:   List of port analysis result objects or dicts.
    route_results:  List of route analysis result objects or dicts.
    insights:       List of Insight objects from the decision engine.
    freight_data:   Dict or list of freight rate records.
    macro_data:     Dict of macro indicator values.
    stock_data:     Dict or list of shipping equity records.
    """
    try:
        apply_dark_layout()
    except Exception:
        pass

    # Coerce None to empty collections so every section can iterate safely
    port_results  = port_results  or []
    route_results = route_results or []
    insights      = insights      or []
    freight_data  = freight_data  or {}
    macro_data    = macro_data    or {}
    stock_data    = stock_data    or {}

    # ── 0. Hero ───────────────────────────────────────────────────────────────
    _render_hero(insights)

    # ── 1. Configuration ──────────────────────────────────────────────────────
    _render_config()

    # ── 2. Generate & Download ────────────────────────────────────────────────
    _render_generate(port_results, route_results, insights, freight_data, macro_data, stock_data)

    # ── 3. Report History ─────────────────────────────────────────────────────
    _render_report_history()

    # ── 4. Report Preview (only after generation) ─────────────────────────────
    _render_preview()

    # ── 5. Key Insights Preview ───────────────────────────────────────────────
    _render_key_insights(insights, macro_data, stock_data)

    # ── 6. News Sentiment Preview ─────────────────────────────────────────────
    _render_news_preview()
