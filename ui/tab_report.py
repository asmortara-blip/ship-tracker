"""Investor Intelligence Report Tab — institutional report generation UI.

Migrated to the canonical design system. See docs/TAB_MIGRATION.md.

Sections
--------
1. Hero header
2. Report Configuration Panel
3. Generate Report button + progress
4. Report Preview Panel (post-generation)
5. Download buttons row
6. Report History
7. Data Source Status Panel
8. API Configuration (collapsed)
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import plotly.graph_objects as go
import streamlit as st

from ui.styles import (
    C_ACCENT,
    C_BG,
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
    section_divider,
    section_header,
    source_footer,
    status_badge,
    wsj_market_table,
)

try:
    from loguru import logger
except Exception:
    import logging
    logger = logging.getLogger(__name__)

try:
    from processing.investor_report_engine import build_investor_report
    _ENGINE_OK = True
except Exception:
    _ENGINE_OK = False
    def build_investor_report(*args, **kwargs):
        raise ImportError("processing.investor_report_engine not available")

try:
    from utils.investor_report_pdf import _FPDF_OK, render_investor_report_pdf
    _PDF_OK = _FPDF_OK
except Exception:
    _PDF_OK = False
    def render_investor_report_pdf(report) -> bytes:
        raise ImportError("utils.investor_report_pdf not available")

try:
    from utils.investor_report_html import render_investor_report_html
    _HTML_OK = True
except Exception:
    _HTML_OK = False
    def render_investor_report_html(report) -> str:
        raise ImportError("utils.investor_report_html not available")

try:
    from utils.excel_export import export_full_report as _export_full_report
    _EXCEL_OK = True
except Exception:
    _EXCEL_OK = False
    def _export_full_report(*args, **kwargs) -> bytes:
        raise ImportError("utils.excel_export not available")

# Markdown export — pure function, no heavy deps, so the try/except
# here is belt-and-braces against the module being deleted or renamed.
try:
    from utils.markdown_export import report_to_markdown as _report_to_markdown
    _MD_OK = True
except Exception:
    _MD_OK = False
    def _report_to_markdown(report_data) -> str:
        raise ImportError("utils.markdown_export not available")

try:
    from utils.report_history import (
        delete_report as _delete_report,
        list_reports as _list_reports,
        load_report_html as _load_report_html,
        save_report as _save_report,
        verify_report_integrity as _verify_report_integrity,
        version_chain as _version_chain,
    )
    _HISTORY_OK = True
except Exception:
    _HISTORY_OK = False
    def _list_reports(): return []
    def _load_report_html(rid): return None
    def _delete_report(rid): return False
    def _save_report(html, obj): return None
    def _version_chain(rid, **kwargs): return []
    def _verify_report_integrity(rid, **kwargs):
        return {"status": "unhashed"}


# ── Cell formatters for wsj_market_table() ────────────────────────────────────
# wsj_market_table renders cell strings as raw HTML inside <td>. These helpers
# only style content (font + conditional color); table CSS handles alignment
# and rule lines. Mirrors the pattern in ui/tab_results.py.

def _mono(value: str, color: str = C_TEXT) -> str:
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-variant-numeric:tabular-nums;">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT2, weight: int = 400) -> str:
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _score_color(score: float) -> str:
    if score >= 0.65:
        return C_HIGH
    if score >= 0.45:
        return C_MOD
    return C_LOW


def _risk_color(level: str) -> str:
    mapping = {"LOW": C_HIGH, "MODERATE": C_MOD, "HIGH": C_LOW, "CRITICAL": "#b91c1c"}
    return mapping.get(str(level).upper(), C_MOD)


def _check_api_keys() -> dict[str, bool]:
    """Return dict of data-source name -> whether a key is configured."""
    sources = {
        "Baltic Exchange":  ("BALTIC_API_KEY",),
        "Clarksons":        ("CLARKSONS_API_KEY",),
        "Bloomberg":        ("BLOOMBERG_API_KEY",),
        "Alpha Vantage":    ("ALPHA_VANTAGE_KEY",),
        "NewsAPI":          ("NEWSAPI_KEY",),
        "FRED / St. Louis": ("FRED_API_KEY",),
        "IEX Cloud":        ("IEX_CLOUD_KEY",),
        "Quandl / Nasdaq":  ("QUANDL_API_KEY", "NASDAQ_DATA_LINK_API_KEY"),
    }
    result = {}
    for name, keys in sources.items():
        found = False
        for k in keys:
            try:
                if st.secrets.get(k) or os.environ.get(k):
                    found = True
                    break
            except Exception:
                if os.environ.get(k):
                    found = True
                    break
        result[name] = found
    return result


def _data_live_count(api_status: dict[str, bool]) -> int:
    return sum(1 for v in api_status.values() if v)


def _report_sources(api_status: dict[str, bool]) -> list[dict]:
    """Build a list of provenance dicts that source_footer accepts."""
    live_count = _data_live_count(api_status)
    if live_count >= 6:
        kind, quality = "live", "good"
    elif live_count >= 3:
        kind, quality = "cached", "stale"
    else:
        kind, quality = "modeled", "demo"
    return [
        {"name": "Investor report engine", "kind": "modeled", "quality": "modeled"},
        {"name": f"{live_count}/{len(api_status)} external feeds", "kind": kind, "quality": quality},
    ]


# ── Section 1: Hero ────────────────────────────────────────────────────────────

def _render_hero(last_generated: str | None) -> None:
    subtitle = (
        f"Generated {last_generated}" if last_generated
        else "Not yet generated this session"
    )
    page_header(
        title="Investor Report Generator",
        subtitle=f"Institutional-grade briefing - {subtitle}",
        badge_text="REPORT",
        badge_color=C_ACCENT,
    )
    metric_card_row(
        [
            {"label": "Report Type", "value": "Institutional", "accent": C_ACCENT},
            {"label": "Sections",    "value": "10 Modules",    "accent": C_HIGH},
            {"label": "Formats",     "value": "PDF / HTML / XLS", "accent": C_MOD},
            {"label": "Engine",      "value": "READY" if _ENGINE_OK else "OFFLINE",
             "accent": C_HIGH if _ENGINE_OK else C_LOW},
        ],
        columns=4,
    )


# ── Section 2: Configuration ──────────────────────────────────────────────────

def _render_config_panel(api_status: dict[str, bool]) -> dict:
    """Render configuration panel. Returns config dict."""
    live_count = _data_live_count(api_status)
    total = len(api_status)
    quality_label = "LIVE" if live_count >= 6 else ("PARTIAL" if live_count >= 3 else "MOCK")
    quality_status = "success" if live_count >= 6 else ("warning" if live_count >= 3 else "danger")

    section_header(
        "Report Configuration",
        f"{quality_label} data - {live_count}/{total} sources configured",
    )

    col_left, col_right = st.columns([1, 1], gap="large")
    config: dict[str, Any] = {}

    with col_left:
        st.markdown('<div class="sub-section-header">Report Scope</div>', unsafe_allow_html=True)
        config["scope"] = st.radio(
            "scope",
            ["Full Report", "Quick Digest", "Signal Focus", "Freight Focus"],
            label_visibility="collapsed",
            key="rep_scope",
        )
        st.markdown('<div class="sub-section-header">Narrative Tone</div>', unsafe_allow_html=True)
        config["tone"] = st.radio(
            "tone",
            ["Formal", "Analytical", "Summary"],
            label_visibility="collapsed",
            key="rep_tone",
        )

    with col_right:
        st.markdown('<div class="sub-section-header">Include Sections</div>', unsafe_allow_html=True)
        sections = {}
        for key, label in [
            ("exec_summary",    "Executive Summary"),
            ("signals",         "Alpha Signals"),
            ("freight_rates",   "Freight Rates"),
            ("macro",           "Macro Environment"),
            ("equity",          "Equity Analysis"),
            ("risk",            "Risk Assessment"),
            ("recommendations", "Recommendations"),
        ]:
            sections[key] = st.checkbox(label, value=True, key=f"rep_sec_{key}")
        config["sections"] = sections

    # Discreet data-quality indicator using shared status_badge
    st.markdown(
        status_badge(f"{quality_label} - {live_count}/{total} sources", status=quality_status),
        unsafe_allow_html=True,
    )
    return config


# ── Section 3: Generate button ────────────────────────────────────────────────

def _render_generate_button(
    config: dict,
    port_results, route_results, insights, freight_data, macro_data, stock_data,
) -> None:
    section_header("Generate Report", "Runs the multi-factor pipeline and builds the institutional briefing.")

    if not _ENGINE_OK:
        st.markdown(
            insight_card_html(
                title="Engine Unavailable",
                score=0.05,
                action="OFFLINE",
                rationale=(
                    "The report engine could not be loaded. Check that "
                    "<code>processing.investor_report_engine</code> is installed."
                ),
                category="SYSTEM",
            ),
            unsafe_allow_html=True,
        )
        return

    clicked = st.button(
        "Generate Investor Report",
        key="btn_generate_report",
        type="primary",
        use_container_width=True,
    )
    if clicked:
        steps = [
            "Analyzing sentiment and signals...",
            "Computing freight and macro factors...",
            "Building narrative and recommendations...",
            "Rendering report structure...",
        ]
        progress_bar = st.progress(0)
        status_box = st.empty()
        try:
            for i, step in enumerate(steps):
                status_box.markdown(
                    status_badge(step, status="info"),
                    unsafe_allow_html=True,
                )
                progress_bar.progress((i + 1) / len(steps))

            # NB: build_investor_report does not accept scope/tone/sections
            # (it always builds the full report). Passing them raised
            # TypeError → "Generation Failed" on every click against the real
            # engine — the manual Generate button never worked. Pass only the
            # data sources the engine actually consumes. (scope/tone/sections
            # remain UI-only presentation prefs the engine doesn't honor yet.)
            report = build_investor_report(
                port_results=port_results,
                route_results=route_results,
                insights=insights,
                freight_data=freight_data,
                macro_data=macro_data,
                stock_data=stock_data,
            )
            st.session_state["investor_report"] = report
            st.session_state["investor_report_ts"] = _now_utc()
            progress_bar.progress(1.0)
            status_box.empty()
            logger.info("Investor report generated successfully.")
            st.rerun()
        except Exception as exc:
            progress_bar.empty()
            status_box.empty()
            logger.error(f"Report generation failed: {exc}")
            st.markdown(
                insight_card_html(
                    title="Generation Failed",
                    score=0.05,
                    action="ERROR",
                    rationale=str(exc),
                    category="SYSTEM",
                ),
                unsafe_allow_html=True,
            )


# ── Section 4: Report preview ──────────────────────────────────────────────────

def _render_report_preview(report: Any, ts: str, api_status: dict[str, bool]) -> None:
    if report is None:
        st.markdown(
            insight_card_html(
                title="No Report Data",
                score=0.05,
                action="EMPTY",
                rationale="Generation may have failed silently.",
                category="SYSTEM",
            ),
            unsafe_allow_html=True,
        )
        return

    try:
        is_dict = isinstance(report, dict)
        sentiment_label = report.get("overall_sentiment_label", "N/A") if is_dict else getattr(report, "overall_sentiment_label", "N/A")
        sentiment_score = report.get("overall_sentiment_score", 0.0) if is_dict else getattr(report, "overall_sentiment_score", 0.0)
        risk_level      = report.get("risk_level", "N/A") if is_dict else getattr(report, "risk_level", "N/A")
        top_pick        = report.get("top_pick", "N/A") if is_dict else getattr(report, "top_pick", "N/A")
        data_quality    = report.get("data_quality", "N/A") if is_dict else getattr(report, "data_quality", "N/A")
        signal_count    = len(report.get("signals", []) if is_dict else getattr(report, "signals", []) or [])
        exec_summary    = (report.get("executive_summary", "") if is_dict else getattr(report, "executive_summary", "")) or ""
    except Exception as exc:
        logger.warning(f"Could not parse report fields: {exc}")
        sentiment_label = risk_level = top_pick = data_quality = "N/A"
        sentiment_score = 0.0
        signal_count = 0
        exec_summary = ""

    try:
        score_val = float(sentiment_score)
    except (TypeError, ValueError):
        score_val = 0.5
    score_color = _score_color(score_val)
    risk_col = _risk_color(str(risk_level))

    section_header("Report Generated", ts)
    metric_card_row(
        [
            {"label": "Sentiment",    "value": sentiment_label, "accent": score_color, "sublabel": f"{score_val:.2f}"},
            {"label": "Signals",      "value": str(signal_count), "accent": C_ACCENT, "sublabel": "detected"},
            {"label": "Risk Level",   "value": str(risk_level), "accent": risk_col},
            {"label": "Top Pick",     "value": str(top_pick),   "accent": C_HIGH},
            {"label": "Data Quality", "value": str(data_quality), "accent": C_MOD},
        ],
        columns=5,
    )

    section_header("Report Sections", "Modules included in this build")
    section_names = [
        "Cover Page", "Executive Summary", "Alpha Signals", "Freight Rates",
        "Macro Environment", "Equity Analysis", "Risk Assessment",
        "Recommendations", "Data Appendix", "Methodology",
    ]
    pills_html = "".join(
        f'<span style="display:inline-block;margin:0 6px 6px 0;">{badge(s, color=C_ACCENT)}</span>'
        for s in section_names
    )
    st.markdown(
        f'<div class="wsj-card">'
        f'<div class="sub-section-header">{len(section_names)} Modules</div>'
        f'<div style="margin-top:2px;">{pills_html}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.markdown(source_footer(_report_sources(api_status)), unsafe_allow_html=True)

    if exec_summary:
        preview = exec_summary[:300] + ("..." if len(exec_summary) > 300 else "")
        section_header(
            "Executive Summary",
            "First paragraph of the generated briefing - full text in the export",
        )
        st.markdown(
            insight_card_html(
                title="Executive Summary",
                score=max(0.0, min(1.0, score_val)),
                action=str(sentiment_label).upper(),
                rationale=preview,
                category="REPORT",
            ),
            unsafe_allow_html=True,
        )
        st.markdown(source_footer(_report_sources(api_status)), unsafe_allow_html=True)


# ── Section 5: Downloads ───────────────────────────────────────────────────────

def _dl_error(exc: Exception) -> None:
    st.markdown(status_badge(f"Error: {exc}", status="danger"), unsafe_allow_html=True)


def _dl_unavailable(message: str) -> None:
    st.markdown(status_badge(message, status="neutral"), unsafe_allow_html=True)


def _render_downloads(report: Any) -> None:
    section_header("Download Report", "Pick a format to export the latest build.")
    # 4 columns now: PDF (primary, wider), HTML, Excel, Markdown.
    col_pdf, col_html, col_xl, col_md = st.columns([2, 1, 1, 1], gap="medium")
    with col_pdf:
        st.markdown(
            '<div class="sub-section-header">PDF &mdash; Print-Ready</div>',
            unsafe_allow_html=True,
        )
        if _PDF_OK and report is not None:
            try:
                pdf_bytes = render_investor_report_pdf(report)
                size_kb = len(pdf_bytes) // 1024
                st.download_button(
                    label=f"Download PDF Report ({size_kb} KB)",
                    data=pdf_bytes,
                    file_name=f"investor_report_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
                    mime="application/pdf",
                    key="dl_pdf", type="primary", use_container_width=True,
                )
            except Exception as exc:
                logger.error(f"PDF render failed: {exc}")
                _dl_error(exc)
        elif not _PDF_OK:
            _dl_unavailable("PDF unavailable - install fpdf2.")
        else:
            _dl_unavailable("Generate a report first.")

    with col_html:
        st.markdown(
            '<div class="sub-section-header">HTML &mdash; Web</div>',
            unsafe_allow_html=True,
        )
        if _HTML_OK and report is not None:
            try:
                html_bytes = render_investor_report_html(report).encode("utf-8")
                size_kb = len(html_bytes) // 1024
                st.download_button(
                    label=f"Download HTML ({size_kb} KB)",
                    data=html_bytes,
                    file_name=f"investor_report_{datetime.now().strftime('%Y%m%d_%H%M')}.html",
                    mime="text/html", key="dl_html", use_container_width=True,
                )
            except Exception as exc:
                logger.error(f"HTML render failed: {exc}")
                _dl_error(exc)
        elif not _HTML_OK:
            _dl_unavailable("HTML renderer unavailable.")
        else:
            _dl_unavailable("Generate a report first.")

    with col_xl:
        st.markdown(
            '<div class="sub-section-header">Excel &mdash; Data</div>',
            unsafe_allow_html=True,
        )
        if _EXCEL_OK and report is not None:
            try:
                xl_bytes = _export_full_report(report)
                size_kb = len(xl_bytes) // 1024
                st.download_button(
                    label=f"Download Excel ({size_kb} KB)",
                    data=xl_bytes,
                    file_name=f"investor_report_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="dl_excel", use_container_width=True,
                )
            except Exception as exc:
                logger.error(f"Excel export failed: {exc}")
                _dl_error(exc)
        elif not _EXCEL_OK:
            _dl_unavailable("Excel export unavailable.")
        else:
            _dl_unavailable("Generate a report first.")

    with col_md:
        # Markdown download — pure function, no extra dependency, so the
        # button is disabled only when no report has been generated yet
        # (matching the PDF/HTML/Excel pattern above). The slug uses the
        # same timestamp shape so a side-by-side download lines up
        # alphabetically with its PDF sibling.
        st.markdown(
            '<div class="sub-section-header">Markdown &mdash; Share</div>',
            unsafe_allow_html=True,
        )
        if _MD_OK and report is not None:
            try:
                md_text = _report_to_markdown(report)
                md_bytes = md_text.encode("utf-8")
                size_kb = max(1, len(md_bytes) // 1024)
                slug = f"investor_report_{datetime.now().strftime('%Y%m%d_%H%M')}"
                st.download_button(
                    label=f"Download as Markdown ({size_kb} KB)",
                    data=md_bytes,
                    file_name=f"{slug}.md",
                    mime="text/markdown",
                    key="dl_md", use_container_width=True,
                )
            except Exception as exc:
                logger.error(f"Markdown export failed: {exc}")
                _dl_error(exc)
        elif not _MD_OK:
            _dl_unavailable("Markdown export unavailable.")
        else:
            _dl_unavailable("Generate a report first.")


# ── Section 6: Report history ─────────────────────────────────────────────────

def _rep_field(rep: Any, name: str, default: Any) -> Any:
    """Read ``name`` from a report record that may be either a dataclass
    (``ReportMeta``) or a plain dict — the history layer supports both
    shapes depending on persistence path, and this helper unifies access.
    """
    if isinstance(rep, dict):
        return rep.get(name, default)
    return getattr(rep, name, default)


def _build_sentiment_trend(reports: list) -> go.Figure:
    """Line chart of recent report sentiment scores over time.

    Each report becomes one marker on a chronological axis (oldest →
    newest left to right) at its ``sentiment_score`` value in [-1, +1].
    Markers are coloured by ``sentiment_label`` — green BULLISH, red
    BEARISH, grey NEUTRAL / MIXED — so the eye can spot regime shifts
    without reading the labels.

    Pure builder — no ``st.*`` calls — exercised by the lock-in tests.
    Empty / None returns an annotated-empty figure.
    """
    fig = go.Figure()
    items = list(reports or [])
    if not items:
        fig.add_annotation(
            text="No historical reports yet",
            xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False,
            font={"color": C_TEXT3, "size": 12},
        )
        apply_dark_layout(fig, title="Sentiment trend", height=200)
        return fig

    # Order oldest→newest using the most reliable timestamp field. The
    # history layer typically returns newest-first; flip it here so the
    # chart reads naturally left-to-right.
    def _ts_key(rep):
        ts = _rep_field(rep, "generated_at",
                        _rep_field(rep, "date",
                                   _rep_field(rep, "created_at", "")))
        return str(ts or "")

    ordered = sorted(items, key=_ts_key)

    x_vals = [_ts_key(r) or f"#{i+1}" for i, r in enumerate(ordered)]
    y_vals = [
        max(-1.0, min(1.0, float(_rep_field(r, "sentiment_score", 0.0) or 0.0)))
        for r in ordered
    ]
    labels = [str(_rep_field(r, "sentiment_label", "—")) for r in ordered]

    def _label_color(label: str) -> str:
        upper = label.upper()
        if "BULL" in upper:
            return C_HIGH
        if "BEAR" in upper:
            return C_LOW
        return C_TEXT2
    colors = [_label_color(lbl) for lbl in labels]

    fig.add_trace(go.Scatter(
        x=x_vals,
        y=y_vals,
        mode="lines+markers",
        line={"color": C_TEXT3, "width": 1.4},
        marker={
            "size": 11,
            "color": colors,
            "line": {"color": C_BG, "width": 1.5},
        },
        text=labels,
        hovertemplate=(
            "<b>%{x}</b><br>"
            "Sentiment: %{text}<br>"
            "Score: %{y:+.2f}<extra></extra>"
        ),
        showlegend=False,
    ))

    # Neutral reference line at 0; subtle dashed style.
    fig.add_hline(
        y=0.0,
        line={"color": "rgba(255,255,255,0.10)", "width": 1, "dash": "dot"},
    )

    apply_dark_layout(
        fig,
        title=f"Sentiment trend — last {len(ordered)} report(s)",
        height=220,
    )
    fig.update_layout(
        xaxis={"title": None, "showgrid": False,
               "tickfont": {"color": C_TEXT3, "size": 9}},
        yaxis={"title": "Sentiment (-1 to +1)", "range": [-1.05, 1.05],
               "gridcolor": "rgba(255,255,255,0.04)",
               "zeroline": False},
        margin={"l": 70, "r": 24, "t": 40, "b": 30},
    )
    return fig


def _integrity_badge(report_id: str) -> str:
    """Return an HTML integrity badge for *report_id* (R121, tamper-evidence).

    Calls ``verify_report_integrity`` to recompute the on-disk report bytes'
    SHA-256 and compare it to the hash stored at issue time:

      * ``verified`` → green "✓ verified" — bytes match what was issued.
      * ``tampered`` → red "⚠ tampered"  — on-disk bytes diverged.
      * ``unhashed`` → grey "– unhashed" — legacy row, no hash to verify.
      * ``missing``  → grey "– missing"  — file gone / unknown in scope.

    Crash-resilient: any verify error degrades to a neutral "– unhashed"
    badge rather than taking down the history row (matches the tab's
    per-row try/except style — verify itself also never raises).
    """
    try:
        res = _verify_report_integrity(report_id)
        status = (res or {}).get("status", "unhashed")
    except Exception as exc:  # noqa: BLE001 — never break the row on verify
        logger.warning(f"integrity badge verify failed for {report_id}: {exc}")
        status = "unhashed"

    if status == "verified":
        return badge("✓ verified", color=C_HIGH)
    if status == "tampered":
        return badge("⚠ tampered", color=C_LOW)
    if status == "missing":
        return badge("– missing", color=C_TEXT3)
    # "unhashed" (legacy) + any unexpected status fall through to neutral.
    return badge("– unhashed", color=C_TEXT3)


def _render_history() -> None:
    if not _HISTORY_OK:
        section_header("Report History", "Previously generated reports")
        st.markdown(
            status_badge("Report history unavailable - utils.report_history not loaded.", status="neutral"),
            unsafe_allow_html=True,
        )
        return

    try:
        reports = _list_reports()
    except Exception as exc:
        logger.warning(f"Could not list reports: {exc}")
        reports = []

    if not reports:
        section_header("Report History", "Previously generated reports")
        st.markdown(
            status_badge("No historical reports saved yet.", status="neutral"),
            unsafe_allow_html=True,
        )
        return

    shown = min(len(reports), 10)
    plural = "report" if shown == 1 else "reports"
    section_header("Report History", f"{shown} most recent {plural} - newest first")

    # Sentiment trend at-a-glance — sits above the detail table so the eye
    # sees the regime shift before scanning per-row sentiment chips.
    try:
        st.plotly_chart(
            _build_sentiment_trend(reports[:10]),
            use_container_width=True,
            config={"displayModeBar": False},
            key="report_sentiment_trend",
        )
    except Exception:
        logger.exception("Report — sentiment trend chart failed")

    # Build a wsj_market_table so the row width tracks the rest of the design system.
    headers = ["Date", "Sentiment", "Quality", "Size", "Integrity"]
    rows: list[list[str]] = []
    download_specs: list[dict] = []
    for i, rep in enumerate(reports[:10]):
        try:
            # _list_reports() returns ReportMeta DATACLASSES — use the shape-
            # agnostic _rep_field, not dict .get() (which raised AttributeError
            # on every row → the per-row except below swallowed it → the entire
            # history table + re-download buttons never rendered, even with
            # saved reports). Field names are the ReportMeta ones.
            rep_id   = _rep_field(rep, "report_id", f"rep_{i}")
            rep_date = _rep_field(rep, "report_date",
                                  _rep_field(rep, "generated_at", "Unknown"))
            rep_sent = _rep_field(rep, "sentiment_label", "—")
            rep_qual = _rep_field(rep, "data_quality", "—")
            rep_size = _rep_field(rep, "file_size_kb", "—")
            sent_color = (
                C_HIGH if "BULL" in str(rep_sent).upper()
                else (C_LOW if "BEAR" in str(rep_sent).upper() else C_MOD)
            )
            rows.append([
                _mono(str(rep_date), color=C_TEXT2),
                badge(str(rep_sent), color=sent_color),
                _sans(str(rep_qual), color=C_TEXT3),
                _mono(f"{rep_size} KB", color=C_TEXT3),
                _integrity_badge(rep_id),
            ])
            download_specs.append({"id": rep_id, "index": i})
        except Exception as exc:
            logger.warning(f"Could not render history row {i}: {exc}")

    if rows:
        wsj_market_table(headers, rows)

        # Resolve which archived reports still have downloadable HTML on disk.
        available: list[tuple[str, int, Any]] = []
        for spec in download_specs:
            rep_id = spec["id"]
            i = spec["index"]
            try:
                html_content = _load_report_html(rep_id)
                if html_content:
                    available.append((rep_id, i, html_content))
            except Exception:
                continue

        # Per-row download buttons laid out in a tidy 3-column grid.
        if available:
            st.markdown(
                '<div class="sub-section-header">Re-download Archived Reports</div>',
                unsafe_allow_html=True,
            )
            for base in range(0, len(available), 3):
                chunk = available[base:base + 3]
                cols = st.columns(3, gap="medium")
                for col, (rep_id, i, html_content) in zip(cols, chunk):
                    with col:
                        st.download_button(
                            label=f"Download {rep_id}",
                            data=html_content.encode("utf-8") if isinstance(html_content, str) else html_content,
                            file_name=f"report_{rep_id}.html",
                            mime="text/html",
                            key=f"dl_hist_{rep_id}_{i}",
                            use_container_width=True,
                        )

    # Per-row public-share controls — uses attribute access on ReportMeta.
    _render_share_links(reports[:10])

    # Immutable version lineage (R119) — show the supersede/amend chain for
    # any report that is itself an amendment or has been amended.
    _render_version_lineage(reports[:10])


def _render_share_links(reports: list) -> None:
    """Render per-report 'Generate share link' / 'Revoke' / 'Regenerate' controls."""
    from utils.report_history import make_public, revoke_public

    if not reports:
        return

    base_url = os.environ.get("SHARE_BASE_URL", "https://your-app-domain").rstrip("/")
    now_utc = datetime.now(timezone.utc)

    st.markdown(
        '<div class="sub-section-header">Public Share Links</div>',
        unsafe_allow_html=True,
    )
    if not os.environ.get("SHARE_BASE_URL"):
        st.caption("Configure SHARE_BASE_URL to display the full URL.")

    for rep in reports:
        try:
            rep_id = getattr(rep, "report_id", None)
            if not rep_id:
                continue
            rep_label = getattr(rep, "report_date", None) or rep_id
            slug = getattr(rep, "public_slug", "") or ""
            expires_raw = getattr(rep, "public_expires_at", "") or ""
            expires_dt = None
            if expires_raw:
                try:
                    expires_dt = datetime.fromisoformat(expires_raw)
                    if expires_dt.tzinfo is None:
                        expires_dt = expires_dt.replace(tzinfo=timezone.utc)
                except Exception:
                    expires_dt = None

            with st.expander(f"Share - {rep_label} ({rep_id[:8]})", expanded=False):
                # State 1: no slug -> Generate
                if not slug:
                    pw_input = st.text_input(
                        "Public password (optional)",
                        type="password",
                        key=f"share_pw_{rep_id}",
                        help=(
                            "Leave blank for a link that anyone with the URL "
                            "can open. Provide a password to additionally "
                            "require it before the report renders."
                        ),
                    )
                    if st.button("Generate share link", key=f"share_{rep_id}"):
                        try:
                            new_slug = make_public(
                                rep_id,
                                expires_in_days=30,
                                password=(pw_input or None),
                            )
                            if new_slug:
                                expires_at = now_utc + timedelta(days=30)
                                lock_note = " (password required)" if pw_input else ""
                                st.success(
                                    f"Share link created{lock_note}. "
                                    f"Expires {expires_at.strftime('%Y-%m-%d %H:%M UTC')}."
                                )
                                st.code(new_slug)
                                st.code(f"{base_url}/r/{new_slug}")
                                st.rerun()
                            else:
                                st.error("Could not generate share link.")
                        except Exception as exc:
                            logger.error(f"make_public failed for {rep_id}: {exc}")
                            st.error(f"Share link error: {exc}")
                # State 3: slug exists but expired -> Regenerate
                elif expires_dt is not None and expires_dt < now_utc:
                    st.warning(f"Link expired on {expires_dt.strftime('%Y-%m-%d %H:%M UTC')}.")
                    st.code(slug)
                    pw_input = st.text_input(
                        "Public password (optional)",
                        type="password",
                        key=f"regen_pw_{rep_id}",
                        help=(
                            "Leave blank to regenerate an open link. Provide "
                            "a password to require it before viewing."
                        ),
                    )
                    if st.button("Regenerate share link", key=f"regen_{rep_id}"):
                        try:
                            new_slug = make_public(
                                rep_id,
                                expires_in_days=30,
                                password=(pw_input or None),
                            )
                            if new_slug:
                                lock_note = " (password required)" if pw_input else ""
                                st.success(f"Share link regenerated{lock_note}.")
                                st.code(new_slug)
                                st.code(f"{base_url}/r/{new_slug}")
                                st.rerun()
                            else:
                                st.error("Could not regenerate share link.")
                        except Exception as exc:
                            logger.error(f"make_public regenerate failed for {rep_id}: {exc}")
                            st.error(f"Share link error: {exc}")
                # State 2: slug exists and valid -> show + Revoke
                else:
                    expiry_str = (
                        expires_dt.strftime("%Y-%m-%d %H:%M UTC")
                        if expires_dt is not None else "unknown"
                    )
                    st.caption(f"Active - expires {expiry_str}")
                    if getattr(rep, "public_password_protected", False):
                        st.caption("Password-protected - viewers must enter the password.")
                    st.code(slug)
                    st.code(f"{base_url}/r/{slug}")
                    if st.button("Revoke", key=f"revoke_{rep_id}"):
                        try:
                            ok = revoke_public(rep_id)
                            if ok:
                                try:
                                    st.toast("Share link revoked.")
                                except Exception:
                                    pass
                                st.success("Share link revoked.")
                                st.rerun()
                            else:
                                st.error("Could not revoke share link.")
                        except Exception as exc:
                            logger.error(f"revoke_public failed for {rep_id}: {exc}")
                            st.error(f"Share link error: {exc}")
        except Exception as exc:
            logger.warning(f"Could not render share row: {exc}")


# ── Section 6a2: Version lineage (immutable supersede/amend chain, R119) ──────

def _render_version_lineage(reports: list) -> None:
    """Render the immutable version chain for each amended report.

    A report can no longer be edited in place — an amendment mints a new
    immutable row that links back to its predecessor (see
    ``utils.report_history.amend_report``). For every displayed report that
    is part of a multi-version lineage (it's an amendment, OR it has been
    amended), this renders a small "Version N · supersedes <id> · amended
    <when>" line plus the ordered chain (oldest → newest) in an expander.

    Crash-resilient: a missing ``version_chain`` (history module not loaded)
    or any per-report error degrades to skipping that row rather than taking
    down the tab.
    """
    if not _HISTORY_OK or not reports:
        return

    # Build the set of reports that actually have a lineage worth showing —
    # either report_version > 1 (this row is an amendment) or it heads a
    # chain that has a later version. We resolve via version_chain so a v1
    # report that HAS been amended is also surfaced.
    lineage_rows: list = []
    seen_heads: set[str] = set()
    for rep in reports:
        try:
            rep_id = getattr(rep, "report_id", None)
            if not rep_id:
                continue
            chain = _version_chain(rep_id)
            if len(chain) <= 1:
                continue
            # De-dup by the chain head so two members of the same chain
            # appearing in the history list don't render the lineage twice.
            head_id = chain[0].report_id if chain else rep_id
            if head_id in seen_heads:
                continue
            seen_heads.add(head_id)
            lineage_rows.append((rep, chain))
        except Exception as exc:
            logger.warning(f"version lineage scan failed for a row: {exc}")

    if not lineage_rows:
        return

    section_header(
        "Version Lineage",
        "Immutable amend/supersede chains - originals are never overwritten",
    )

    for rep, chain in lineage_rows:
        try:
            latest = chain[-1]
            head = chain[0]
            label = getattr(head, "report_date", None) or head.report_id
            with st.expander(
                f"Lineage - {label} ({len(chain)} versions, "
                f"latest v{latest.report_version})",
                expanded=False,
            ):
                for m in chain:
                    sup = getattr(m, "supersedes_id", "") or ""
                    when = getattr(m, "generated_at", "") or ""
                    sup_note = (
                        f"supersedes {sup[:8]}" if sup else "original (no predecessor)"
                    )
                    line = (
                        f"Version {m.report_version} - {sup_note} - "
                        f"amended {when}  [{m.report_id[:8]}]"
                    )
                    st.markdown(
                        _mono(line, color=C_TEXT2),
                        unsafe_allow_html=True,
                    )
        except Exception as exc:
            logger.warning(f"Could not render version lineage row: {exc}")


# ── Section 6b: Compare two reports (report-to-report diff) ───────────────────

def _render_diff_panel() -> None:
    """Render a "Compare to a previous report" selector + diff viewer.

    Operators no longer have to open two browser tabs to spot what
    changed — they pick a current report and a prior report from
    selectboxes, click "Compute diff", and the structured diff
    renders in an expander below with a Markdown-download button.

    Lazy-imports ``utils.report_diff`` so a missing module degrades
    to a "diff unavailable" notice rather than crashing the tab.
    """
    section_header(
        "Compare to a Previous Report",
        "Pick two reports and see what changed — signals, routes, sentiment.",
    )

    if not _HISTORY_OK:
        st.markdown(
            status_badge(
                "Compare unavailable - utils.report_history not loaded.",
                status="neutral",
            ),
            unsafe_allow_html=True,
        )
        return

    try:
        from utils.report_diff import (
            diff_reports,
            load_report_payload,
            render_diff_html,
            render_diff_markdown,
        )
    except Exception as exc:
        logger.warning(f"report_diff import failed: {exc}")
        st.markdown(
            status_badge(
                "Compare unavailable - utils.report_diff not loaded.",
                status="neutral",
            ),
            unsafe_allow_html=True,
        )
        return

    try:
        reports = _list_reports()
    except Exception as exc:
        logger.warning(f"_render_diff_panel: list_reports failed: {exc}")
        reports = []

    if len(reports) < 2:
        st.markdown(
            status_badge(
                "Need at least two saved reports to compute a diff.",
                status="neutral",
            ),
            unsafe_allow_html=True,
        )
        return

    # Cap the dropdowns at the 20 most-recent reports so a long-running
    # account doesn't push the selectbox into an unscrollable mess.
    options = reports[:20]
    labels = {
        r.report_id: f"{r.report_date} ({r.report_id[:8]}) - {r.sentiment_label}"
        for r in options
    }

    col_a, col_b = st.columns(2, gap="medium")
    with col_a:
        st.markdown(
            '<div class="sub-section-header">Report A (older)</div>',
            unsafe_allow_html=True,
        )
        # Default A to the second-most-recent so the natural diff is
        # "previous vs current". A user can pick anything from the 20
        # most-recent though.
        default_a_idx = 1 if len(options) > 1 else 0
        a_id = st.selectbox(
            "report_a",
            options=[r.report_id for r in options],
            index=default_a_idx,
            format_func=lambda rid: labels.get(rid, rid[:8]),
            label_visibility="collapsed",
            key="rep_diff_a",
        )
    with col_b:
        st.markdown(
            '<div class="sub-section-header">Report B (newer)</div>',
            unsafe_allow_html=True,
        )
        b_id = st.selectbox(
            "report_b",
            options=[r.report_id for r in options],
            index=0,
            format_func=lambda rid: labels.get(rid, rid[:8]),
            label_visibility="collapsed",
            key="rep_diff_b",
        )

    if a_id == b_id:
        st.markdown(
            status_badge(
                "Pick two different reports to compute a diff.",
                status="warning",
            ),
            unsafe_allow_html=True,
        )
        return

    clicked = st.button(
        "Compute diff",
        key="btn_compute_diff",
        type="primary",
        use_container_width=True,
    )
    if not clicked:
        return

    try:
        payload_a = load_report_payload(a_id)
        payload_b = load_report_payload(b_id)
    except Exception as exc:
        logger.error(f"load_report_payload failed: {exc}")
        st.error("Could not load report payloads.")
        return

    if payload_a is None or payload_b is None:
        st.error("One or both reports could not be loaded in your scope.")
        return

    try:
        diff = diff_reports(
            payload_a, payload_b,
            report_a_id=a_id, report_b_id=b_id,
        )
    except Exception as exc:
        logger.error(f"diff_reports failed: {exc}")
        st.error("Diff computation failed.")
        return

    with st.expander("Diff results", expanded=True):
        try:
            st.markdown(render_diff_html(diff), unsafe_allow_html=True)
        except Exception as exc:
            logger.warning(f"render_diff_html failed: {exc}")
            st.error("Could not render diff HTML.")

        try:
            md = render_diff_markdown(diff)
            st.download_button(
                label="Download as Markdown",
                data=md.encode("utf-8"),
                file_name=f"diff_{a_id[:8]}_to_{b_id[:8]}.md",
                mime="text/markdown",
                key=f"dl_diff_{a_id}_{b_id}",
                use_container_width=True,
            )
        except Exception as exc:
            logger.warning(f"diff markdown download failed: {exc}")


# ── Section 7: Data source status ─────────────────────────────────────────────

def _render_data_sources(api_status: dict[str, bool]) -> None:
    live_count = _data_live_count(api_status)
    total = len(api_status)
    section_header(
        "Data Source Status",
        f"{live_count} of {total} sources live - full diagnostics in Data Health tab",
    )
    headers = ["Source", "Status"]
    rows = []
    for name, is_live in api_status.items():
        rows.append([
            _sans(name, color=C_TEXT, weight=600),
            badge("LIVE" if is_live else "OFFLINE", color=C_HIGH if is_live else C_LOW),
        ])
    wsj_market_table(headers, rows)
    st.markdown(source_footer(_report_sources(api_status)), unsafe_allow_html=True)


# ── Section 7b: Recurring schedules ───────────────────────────────────────────

def _render_schedules() -> None:
    """Schedule-recurring-report panel.

    Lets an operator create / enable / disable / delete a cron-driven
    schedule that auto-generates this report at a configurable cadence.
    Persistence flows through ``engine.report_scheduler``; the worker
    fires due schedules via ``run_report_scheduler_job``.

    The expander defaults to collapsed so it doesn't dominate the
    surrounding history section. Crash-resilient — every render block
    is wrapped in try/except so a SQLite outage doesn't take down the
    Reports tab.
    """
    try:
        from engine.report_scheduler import (
            ReportSchedule,
            delete_schedule,
            load_schedules,
            new_schedule_id,
            save_schedule,
            validate_cron_expr,
        )
    except Exception as exc:
        logger.warning(f"_render_schedules: import failed: {exc}")
        st.markdown(
            status_badge(
                "Recurring schedules unavailable - engine module missing.",
                status="neutral",
            ),
            unsafe_allow_html=True,
        )
        return

    section_header(
        "Recurring schedules",
        "Auto-generate this report on a cron-like schedule",
    )

    # ── Create-new form ──────────────────────────────────────────
    try:
        from state.user_scope import current_user_id
        uid = current_user_id()
    except Exception:
        uid = ""

    with st.expander("Schedule recurring report...", expanded=False):
        name = st.text_input(
            "Name",
            key="schedule_new_name",
            help="A short label for this schedule, e.g. 'Morning Macro'.",
        )
        cron_expr = st.text_input(
            "Cron expression",
            value="0 9 * * *",
            key="schedule_new_cron",
            help='5-field cron: "minute hour day-of-month month day-of-week". '
                 'Supports *, */N, single ints, comma-lists. Ranges (1-5) not supported.',
        )
        enabled = st.checkbox(
            "Enabled", value=True, key="schedule_new_enabled",
            help="When checked the worker will fire this schedule on the cadence above.",
        )

        # Inline cron validation — coloured feedback below the input.
        ok, err = validate_cron_expr(cron_expr or "")
        if cron_expr:
            if ok:
                st.markdown(
                    f'<span style="color:{C_HIGH};font-family:var(--mono);">'
                    f'Valid cron expression.</span>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f'<span style="color:{C_LOW};font-family:var(--mono);">'
                    f'Invalid: {err}</span>',
                    unsafe_allow_html=True,
                )

        if st.button("Save schedule", key="schedule_save_btn", disabled=not ok):
            if not (name or "").strip():
                st.error("Name is required.")
            elif not ok:
                st.error(f"Cannot save — invalid cron: {err}")
            else:
                sched = ReportSchedule(
                    schedule_id=new_schedule_id(),
                    user_id=uid,
                    name=name.strip(),
                    cron_expr=cron_expr.strip(),
                    enabled=bool(enabled),
                )
                if save_schedule(sched):
                    st.success(f"Saved schedule '{sched.name}' (next: {sched.next_run_at or '...'})")
                    try:
                        st.experimental_rerun()
                    except Exception:
                        pass
                else:
                    st.error("Could not save schedule. Check logs.")

    # ── Existing-schedule list ───────────────────────────────────
    try:
        schedules = load_schedules(user_id=uid)
    except Exception as exc:
        logger.warning(f"_render_schedules: load failed: {exc}")
        schedules = []

    if not schedules:
        st.markdown(
            status_badge("No recurring schedules yet.", status="neutral"),
            unsafe_allow_html=True,
        )
        return

    for sched in schedules:
        try:
            cols = st.columns([3, 2, 1, 2, 1, 1, 1])
            with cols[0]:
                st.markdown(
                    _sans(sched.name or "(no name)", color=C_TEXT, weight=600),
                    unsafe_allow_html=True,
                )
                st.markdown(
                    _mono(sched.cron_expr, color=C_TEXT3),
                    unsafe_allow_html=True,
                )
            with cols[1]:
                st.markdown(
                    _sans("Next run:", color=C_TEXT3),
                    unsafe_allow_html=True,
                )
                st.markdown(
                    _mono(sched.next_run_at or "(none)", color=C_TEXT2),
                    unsafe_allow_html=True,
                )
            with cols[2]:
                status_color = (
                    C_HIGH if sched.last_run_status == "ok"
                    else C_LOW if sched.last_run_status == "error"
                    else C_TEXT3
                )
                st.markdown(
                    _sans(sched.last_run_status or "never",
                          color=status_color, weight=600),
                    unsafe_allow_html=True,
                )
            with cols[3]:
                st.markdown(
                    _sans("Enabled:", color=C_TEXT3),
                    unsafe_allow_html=True,
                )
                st.markdown(
                    _sans("Yes" if sched.enabled else "No",
                          color=C_HIGH if sched.enabled else C_LOW,
                          weight=600),
                    unsafe_allow_html=True,
                )
            with cols[4]:
                if sched.enabled:
                    if st.button("Disable", key=f"sched_disable_{sched.schedule_id}"):
                        sched.enabled = False
                        if save_schedule(sched):
                            st.success("Disabled.")
                            try:
                                st.experimental_rerun()
                            except Exception:
                                pass
                else:
                    if st.button("Enable", key=f"sched_enable_{sched.schedule_id}"):
                        sched.enabled = True
                        if save_schedule(sched):
                            st.success("Enabled.")
                            try:
                                st.experimental_rerun()
                            except Exception:
                                pass
            with cols[5]:
                if st.button("Delete", key=f"sched_delete_{sched.schedule_id}"):
                    if delete_schedule(sched.schedule_id, user_id=uid):
                        st.success("Deleted.")
                        try:
                            st.experimental_rerun()
                        except Exception:
                            pass
                    else:
                        st.error("Could not delete schedule.")
        except Exception as exc:
            logger.warning(f"_render_schedules: row render failed: {exc}")


# ── Section 8: API config ─────────────────────────────────────────────────────

def _render_api_config(api_status: dict[str, bool]) -> None:
    with st.expander("API Configuration", expanded=False):
        st.markdown(
            '<div class="sub-section-header">Premium Feed Keys</div>'
            + _sans(
                "Configure API keys via st.secrets (secrets.toml) or environment variables. "
                "Keys are never displayed - only their presence is checked.",
                color=C_TEXT2,
            ),
            unsafe_allow_html=True,
        )
        key_map = {
            "Baltic Exchange":  "BALTIC_API_KEY",
            "Clarksons":        "CLARKSONS_API_KEY",
            "Bloomberg":        "BLOOMBERG_API_KEY",
            "Alpha Vantage":    "ALPHA_VANTAGE_KEY",
            "NewsAPI":          "NEWSAPI_KEY",
            "FRED / St. Louis": "FRED_API_KEY",
            "IEX Cloud":        "IEX_CLOUD_KEY",
            "Quandl / Nasdaq":  "QUANDL_API_KEY",
        }
        rows = []
        for name, env_key in key_map.items():
            is_set = api_status.get(name, False)
            status_color = C_HIGH if is_set else C_LOW
            status_txt   = "Configured" if is_set else "Missing"
            rows.append([
                _sans(name, color=C_TEXT, weight=600),
                _mono(env_key, color=C_TEXT3),
                _sans(status_txt, color=status_color, weight=700),
            ])
        wsj_market_table(["Source", "Env Var", "Status"], rows)
        st.markdown(
            '<div style="margin-top:8px;">'
            + _sans("Add keys to ", color=C_TEXT3)
            + _mono(".streamlit/secrets.toml", color=C_TEXT2)
            + _sans(" &mdash; e.g. ", color=C_TEXT3)
            + _mono('ALPHA_VANTAGE_KEY = "your-key-here"', color=C_TEXT2)
            + _sans(" &mdash; or export the matching environment variable "
                    "before launching the app.", color=C_TEXT3)
            + "</div>",
            unsafe_allow_html=True,
        )


# ── Main render ────────────────────────────────────────────────────────────────

def render(
    port_results=None,
    route_results=None,
    insights=None,
    freight_data=None,
    macro_data=None,
    stock_data=None,
    *args,
    **kwargs,
) -> None:
    """Render the Investor Report tab."""
    # Lazy import keeps perf_telemetry off the tab-load critical path.
    from engine.perf_telemetry import track_render
    
    with track_render('report'):
        report     = st.session_state.get("investor_report")
        last_ts    = st.session_state.get("investor_report_ts")
        api_status = _check_api_keys()

        try:
            _render_hero(last_ts)
        except Exception as exc:
            logger.error(f"Hero render error: {exc}")
            st.error("Could not render header.")

        section_divider("Configure")

        config: dict = {}
        try:
            config = _render_config_panel(api_status)
        except Exception as exc:
            logger.error(f"Config panel error: {exc}")
            st.error("Could not render configuration panel.")

        try:
            _render_generate_button(config, port_results, route_results, insights, freight_data, macro_data, stock_data)
        except Exception as exc:
            logger.error(f"Generate button error: {exc}")
            st.error("Could not render generate button.")

        if report is not None:
            section_divider("Briefing")
            try:
                _render_report_preview(report, last_ts or _now_utc(), api_status)
            except Exception as exc:
                logger.error(f"Report preview error: {exc}")
                st.error("Could not render report preview.")
            section_divider("Export")
            try:
                _render_downloads(report)
            except Exception as exc:
                logger.error(f"Download section error: {exc}")
                st.error("Could not render download buttons.")
        elif last_ts is not None:
            st.markdown(
                insight_card_html(
                    title="Report Data Is None",
                    score=0.05,
                    action="EMPTY",
                    rationale="The engine ran but returned no data. Check logs for details.",
                    category="SYSTEM",
                ),
                unsafe_allow_html=True,
            )
        else:
            section_divider("Export")
            try:
                _render_downloads(None)
            except Exception as exc:
                logger.error(f"Download placeholder error: {exc}")

        section_divider("History")
        try:
            _render_history()
        except Exception as exc:
            logger.error(f"History render error: {exc}")
            st.error("Could not render report history.")

        section_divider("Compare")
        try:
            _render_diff_panel()
        except Exception as exc:
            logger.error(f"Diff panel error: {exc}")
            st.error("Could not render compare panel.")

        section_divider("Schedules")
        try:
            _render_schedules()
        except Exception as exc:
            logger.error(f"Schedules render error: {exc}")
            st.error("Could not render schedules panel.")

        section_divider("Data Sources")
        try:
            _render_data_sources(api_status)
        except Exception as exc:
            logger.error(f"Data source status error: {exc}")
            st.error("Could not render data source status.")

        try:
            _render_api_config(api_status)
        except Exception as exc:
            logger.error(f"API config render error: {exc}")
            st.error("Could not render API configuration.")
