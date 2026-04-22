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
from datetime import datetime, timezone
from typing import Any

import streamlit as st

from ui.styles import (
    C_ACCENT,
    C_BORDER,
    C_CARD,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_SURFACE,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    badge,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
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

try:
    from utils.report_history import (
        delete_report as _delete_report,
        list_reports as _list_reports,
        load_report_html as _load_report_html,
        save_report as _save_report,
    )
    _HISTORY_OK = True
except Exception:
    _HISTORY_OK = False
    def _list_reports(): return []
    def _load_report_html(rid): return None
    def _delete_report(rid): return False
    def _save_report(html, obj): return None


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


# ── Section 1: Hero ────────────────────────────────────────────────────────────

def _render_hero(last_generated: str | None) -> None:
    subtitle = (
        f"Generated {last_generated}" if last_generated
        else "Not yet generated this session"
    )
    page_header(
        title="Investor Report Generator",
        subtitle=f"Institutional-grade briefing · {subtitle}",
        badge_text="MULTI-FACTOR ANALYSIS",
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
    quality_color = C_HIGH if live_count >= 6 else (C_MOD if live_count >= 3 else C_LOW)
    quality_label = "LIVE" if live_count >= 6 else ("PARTIAL" if live_count >= 3 else "MOCK")

    section_header(
        "Report Configuration",
        f"{quality_label} data — {live_count}/{total} sources configured",
    )

    col_left, col_right = st.columns([1, 1], gap="large")
    config: dict[str, Any] = {}

    with col_left:
        st.html(
            f'<div style="font-size:11px;color:{C_TEXT3};letter-spacing:1px;margin-bottom:6px;">REPORT SCOPE</div>'
        )
        config["scope"] = st.radio(
            "scope",
            ["Full Report", "Quick Digest", "Signal Focus", "Freight Focus"],
            label_visibility="collapsed",
            key="rep_scope",
        )
        st.html(
            f'<div style="font-size:11px;color:{C_TEXT3};letter-spacing:1px;margin:12px 0 6px;">NARRATIVE TONE</div>'
        )
        config["tone"] = st.radio(
            "tone",
            ["Formal", "Analytical", "Summary"],
            label_visibility="collapsed",
            key="rep_tone",
        )

    with col_right:
        st.html(
            f'<div style="font-size:11px;color:{C_TEXT3};letter-spacing:1px;margin-bottom:6px;">INCLUDE SECTIONS</div>'
        )
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

    # Discreet data-quality indicator row
    st.html(
        f'<div style="display:flex;align-items:center;gap:8px;margin-top:12px;">'
        f'<div style="width:8px;height:8px;border-radius:50%;background:{quality_color};'
        f'box-shadow:0 0 6px {quality_color};"></div>'
        f'<span style="font-size:11px;color:{quality_color};font-weight:700;letter-spacing:1px;">'
        f'{quality_label} — {live_count}/{total} sources</span></div>'
    )
    return config


# ── Section 3: Generate button ────────────────────────────────────────────────

def _render_generate_button(
    config: dict,
    port_results, route_results, insights, freight_data, macro_data, stock_data,
) -> None:
    section_header("Generate Report", "Runs the multi-factor pipeline and builds the institutional briefing.")

    if not _ENGINE_OK:
        st.html(
            f'<div style="background:rgba(192,57,43,0.1);border:1px solid rgba(192,57,43,0.3);'
            f'border-radius:8px;padding:14px 18px;color:{C_LOW};font-size:13px;margin-bottom:16px;">'
            f'<strong>Engine unavailable.</strong> The report engine could not be loaded. '
            f'Check that <code>processing.investor_report_engine</code> is installed.</div>'
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
                status_box.html(
                    f'<div style="text-align:center;color:{C_TEXT2};font-size:13px;padding:8px;">'
                    f'<span style="color:{C_ACCENT};font-weight:700;">&#9654;</span> {step}</div>'
                )
                progress_bar.progress((i + 1) / len(steps))

            report = build_investor_report(
                port_results=port_results,
                route_results=route_results,
                insights=insights,
                freight_data=freight_data,
                macro_data=macro_data,
                stock_data=stock_data,
                scope=config.get("scope", "Full Report"),
                tone=config.get("tone", "Formal"),
                sections=config.get("sections", {}),
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
            st.html(
                f'<div style="background:rgba(192,57,43,0.1);border:1px solid rgba(192,57,43,0.35);'
                f'border-radius:8px;padding:16px 20px;margin-top:12px;">'
                f'<div style="color:{C_LOW};font-weight:700;font-size:13px;margin-bottom:4px;">Generation Failed</div>'
                f'<div style="color:{C_TEXT2};font-size:12px;">{exc}</div></div>'
            )


# ── Section 4: Report preview ──────────────────────────────────────────────────

def _render_report_preview(report: Any, ts: str) -> None:
    if report is None:
        st.html(
            f'<div style="background:rgba(192,57,43,0.08);border:1px solid rgba(192,57,43,0.25);'
            f'border-radius:8px;padding:16px 20px;color:{C_LOW};font-size:13px;">'
            f'<strong>No report data.</strong> Generation may have failed silently.</div>'
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

    section_names = [
        "Cover Page", "Executive Summary", "Alpha Signals", "Freight Rates",
        "Macro Environment", "Equity Analysis", "Risk Assessment",
        "Recommendations", "Data Appendix", "Methodology",
    ]
    pills_html = "".join(
        f'<span style="background:rgba(53,114,176,0.12);border:1px solid rgba(53,114,176,0.25);'
        f'color:{C_TEXT2};font-size:11px;padding:4px 10px;border-radius:20px;">{s}</span>'
        for s in section_names
    )
    st.html(
        f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:6px;'
        f'padding:16px 20px;margin-bottom:20px;">'
        f'<div style="font-size:10px;color:{C_TEXT3};letter-spacing:1px;margin-bottom:10px;">REPORT SECTIONS</div>'
        f'<div style="display:flex;flex-wrap:wrap;gap:6px;">{pills_html}</div></div>'
    )

    if exec_summary:
        preview = exec_summary[:300] + ("…" if len(exec_summary) > 300 else "")
        st.html(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:6px;'
            f'padding:18px 22px;margin-bottom:20px;">'
            f'<div style="font-size:10px;color:{C_TEXT3};letter-spacing:1px;margin-bottom:10px;">'
            f'EXECUTIVE SUMMARY — PREVIEW</div>'
            f'<div style="font-size:13px;color:{C_TEXT};line-height:1.7;font-style:italic;">{preview}</div></div>'
        )


# ── Section 5: Downloads ───────────────────────────────────────────────────────

def _dl_error(exc: Exception) -> None:
    st.html(
        f'<div style="color:{C_LOW};font-size:11px;padding:4px;">Error: {exc}</div>'
    )


def _render_downloads(report: Any) -> None:
    st.html(
        f'<div style="font-size:12px;font-weight:700;letter-spacing:2px;color:{C_TEXT};'
        f'margin-bottom:12px;">DOWNLOAD REPORT</div>'
    )
    col_pdf, col_html, col_xl = st.columns([2, 1, 1], gap="medium")
    with col_pdf:
        if _PDF_OK and report is not None:
            try:
                pdf_bytes = render_investor_report_pdf(report)
                size_kb = len(pdf_bytes) // 1024
                st.download_button(
                    label=f"Download PDF Report  ({size_kb} KB)",
                    data=pdf_bytes,
                    file_name=f"investor_report_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
                    mime="application/pdf",
                    key="dl_pdf", type="primary", use_container_width=True,
                )
            except Exception as exc:
                logger.error(f"PDF render failed: {exc}")
                _dl_error(exc)
        elif not _PDF_OK:
            st.html(
                f'<div style="background:rgba(100,116,139,0.1);border:1px solid rgba(100,116,139,0.2);'
                f'border-radius:8px;padding:12px 16px;color:{C_TEXT3};font-size:12px;">'
                f'PDF unavailable — install <code>fpdf2</code>.</div>'
            )
        else:
            st.html(f'<div style="color:{C_TEXT3};font-size:12px;padding:12px;">Generate a report first.</div>')

    with col_html:
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
            st.html(f'<div style="color:{C_TEXT3};font-size:11px;padding:4px;">HTML renderer unavailable.</div>')
        else:
            st.html(f'<div style="color:{C_TEXT3};font-size:11px;padding:4px;">Generate a report first.</div>')

    with col_xl:
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
            st.html(f'<div style="color:{C_TEXT3};font-size:11px;padding:4px;">Excel export unavailable.</div>')
        else:
            st.html(f'<div style="color:{C_TEXT3};font-size:11px;padding:4px;">Generate a report first.</div>')


# ── Section 6: Report history ─────────────────────────────────────────────────

def _render_history() -> None:
    section_header("Report History", "Previously generated reports")

    if not _HISTORY_OK:
        st.html(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:8px;'
            f'padding:14px 18px;color:{C_TEXT3};font-size:12px;">'
            f'Report history unavailable — <code>utils.report_history</code> not loaded.</div>'
        )
        return

    try:
        reports = _list_reports()
    except Exception as exc:
        logger.warning(f"Could not list reports: {exc}")
        reports = []

    if not reports:
        st.html(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:8px;'
            f'padding:14px 18px;color:{C_TEXT3};font-size:12px;">No historical reports saved yet.</div>'
        )
        return

    for i, rep in enumerate(reports[:10]):
        try:
            rep_id   = rep.get("id", f"rep_{i}")
            rep_date = rep.get("date", rep.get("created_at", "Unknown"))
            rep_sent = rep.get("sentiment_label", rep.get("sentiment", "—"))
            rep_qual = rep.get("data_quality", "—")
            rep_size = rep.get("file_size_kb", rep.get("size_kb", "—"))
            sent_color = C_HIGH if "BULL" in str(rep_sent).upper() else (C_LOW if "BEAR" in str(rep_sent).upper() else C_MOD)

            col_info, col_btn = st.columns([4, 1], gap="small")
            with col_info:
                st.html(
                    f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:8px;'
                    f'padding:12px 16px;display:flex;align-items:center;gap:16px;">'
                    f'<span style="font-size:12px;color:{C_TEXT2};font-family:var(--mono);">{rep_date}</span>'
                    f'{badge(str(rep_sent), color=sent_color)}'
                    f'<span style="font-size:11px;color:{C_TEXT3};">Quality: {rep_qual}</span>'
                    f'<span style="font-size:11px;color:{C_TEXT3};margin-left:auto;">{rep_size} KB</span>'
                    f'</div>'
                )
            with col_btn:
                try:
                    html_content = _load_report_html(rep_id)
                    if html_content:
                        st.download_button(
                            label="Download",
                            data=html_content.encode("utf-8") if isinstance(html_content, str) else html_content,
                            file_name=f"report_{rep_id}.html",
                            mime="text/html",
                            key=f"dl_hist_{rep_id}_{i}",
                            use_container_width=True,
                        )
                    else:
                        st.html(f'<div style="color:{C_TEXT3};font-size:11px;padding:8px;">Unavailable</div>')
                except Exception:
                    st.html(f'<div style="color:{C_TEXT3};font-size:11px;padding:8px;">—</div>')
        except Exception as exc:
            logger.warning(f"Could not render history row {i}: {exc}")


# ── Section 7: Data source status ─────────────────────────────────────────────

def _render_data_sources(api_status: dict[str, bool]) -> None:
    live_count = _data_live_count(api_status)
    total = len(api_status)
    summary_color = C_HIGH if live_count >= 6 else (C_MOD if live_count >= 3 else C_LOW)
    section_header(
        "Data Source Status",
        f"{live_count} of {total} sources live — full diagnostics in Data Health tab",
    )
    items_html = ""
    for name, is_live in api_status.items():
        dot_color = C_HIGH if is_live else C_LOW
        label = "LIVE" if is_live else "OFFLINE"
        items_html += (
            f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:8px;'
            f'padding:12px 14px;display:flex;align-items:center;gap:10px;">'
            f'<div style="width:8px;height:8px;border-radius:50%;background:{dot_color};'
            f'box-shadow:0 0 5px {dot_color};flex-shrink:0;"></div>'
            f'<div style="flex:1;">'
            f'<div style="font-size:12px;color:{C_TEXT};font-weight:600;">{name}</div>'
            f'<div style="font-size:10px;color:{dot_color};letter-spacing:1px;">{label}</div>'
            f'</div></div>'
        )
    st.html(
        f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:6px;'
        f'padding:22px 26px;">'
        f'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">'
        f'<div style="font-size:12px;font-weight:700;letter-spacing:2px;color:{C_TEXT};">SOURCES</div>'
        f'<span style="font-size:12px;font-weight:700;color:{summary_color};">'
        f'{live_count} of {total} live</span></div>'
        f'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;">{items_html}</div>'
        f'</div>'
    )


# ── Section 8: API config ─────────────────────────────────────────────────────

def _render_api_config(api_status: dict[str, bool]) -> None:
    with st.expander("API Configuration", expanded=False):
        st.html(
            f'<div style="font-size:12px;color:{C_TEXT2};margin-bottom:14px;line-height:1.6;">'
            f'Configure API keys via <code>st.secrets</code> (secrets.toml) or environment variables. '
            f'Keys are never displayed — only their presence is checked.</div>'
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
        rows_html = ""
        for name, env_key in key_map.items():
            is_set = api_status.get(name, False)
            status_color = C_HIGH if is_set else C_LOW
            status_txt   = "Configured" if is_set else "Missing"
            rows_html += (
                f'<div style="display:flex;align-items:center;gap:12px;padding:8px 0;'
                f'border-bottom:1px solid {C_BORDER};">'
                f'<div style="width:8px;height:8px;border-radius:50%;background:{status_color};flex-shrink:0;"></div>'
                f'<div style="flex:1;font-size:12px;color:{C_TEXT};">{name}</div>'
                f'<code style="font-size:11px;color:{C_TEXT3};">{env_key}</code>'
                f'<span style="font-size:11px;color:{status_color};font-weight:600;">{status_txt}</span>'
                f'</div>'
            )
        st.html(
            f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:8px;'
            f'padding:12px 16px;">{rows_html}</div>'
        )
        st.html(
            f'<div style="margin-top:12px;font-size:11px;color:{C_TEXT3};line-height:1.7;">'
            f'Add keys to <code>.streamlit/secrets.toml</code>:<br>'
            f'<code>ALPHA_VANTAGE_KEY = "your-key-here"</code><br>'
            f'Or set environment variables before launching the app.</div>'
        )


# ── Main render ────────────────────────────────────────────────────────────────

def render(
    port_results=None,
    route_results=None,
    insights=None,
    freight_data=None,
    macro_data=None,
    stock_data=None,
) -> None:
    """Render the Investor Report tab."""
    report     = st.session_state.get("investor_report")
    last_ts    = st.session_state.get("investor_report_ts")
    api_status = _check_api_keys()

    try:
        _render_hero(last_ts)
    except Exception as exc:
        logger.error(f"Hero render error: {exc}")
        st.error("Could not render header.")

    section_divider()

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
        section_divider()
        try:
            _render_report_preview(report, last_ts or _now_utc())
        except Exception as exc:
            logger.error(f"Report preview error: {exc}")
            st.error("Could not render report preview.")
        try:
            _render_downloads(report)
        except Exception as exc:
            logger.error(f"Download section error: {exc}")
            st.error("Could not render download buttons.")
    elif last_ts is not None:
        st.html(
            f'<div style="background:rgba(192,57,43,0.08);border:1px solid rgba(192,57,43,0.25);'
            f'border-radius:8px;padding:16px 20px;color:{C_LOW};font-size:13px;margin-top:16px;">'
            f'<strong>Report data is None.</strong> The engine ran but returned no data. '
            f'Check logs for details.</div>'
        )
    else:
        try:
            _render_downloads(None)
        except Exception as exc:
            logger.error(f"Download placeholder error: {exc}")

    section_divider()
    try:
        _render_history()
    except Exception as exc:
        logger.error(f"History render error: {exc}")
        st.error("Could not render report history.")

    section_divider()
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
