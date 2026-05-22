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

import streamlit as st

from ui.styles import (
    C_ACCENT,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
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
    col_pdf, col_html, col_xl = st.columns([2, 1, 1], gap="medium")
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


# ── Section 6: Report history ─────────────────────────────────────────────────

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

    # Build a wsj_market_table so the row width tracks the rest of the design system.
    headers = ["Date", "Sentiment", "Quality", "Size"]
    rows: list[list[str]] = []
    download_specs: list[dict] = []
    for i, rep in enumerate(reports[:10]):
        try:
            rep_id   = rep.get("id", f"rep_{i}")
            rep_date = rep.get("date", rep.get("created_at", "Unknown"))
            rep_sent = rep.get("sentiment_label", rep.get("sentiment", "—"))
            rep_qual = rep.get("data_quality", "—")
            rep_size = rep.get("file_size_kb", rep.get("size_kb", "—"))
            sent_color = (
                C_HIGH if "BULL" in str(rep_sent).upper()
                else (C_LOW if "BEAR" in str(rep_sent).upper() else C_MOD)
            )
            rows.append([
                _mono(str(rep_date), color=C_TEXT2),
                badge(str(rep_sent), color=sent_color),
                _sans(str(rep_qual), color=C_TEXT3),
                _mono(f"{rep_size} KB", color=C_TEXT3),
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
                    if st.button("Generate share link", key=f"share_{rep_id}"):
                        try:
                            new_slug = make_public(rep_id, expires_in_days=30)
                            if new_slug:
                                expires_at = now_utc + timedelta(days=30)
                                st.success(f"Share link created. Expires {expires_at.strftime('%Y-%m-%d %H:%M UTC')}.")
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
                    if st.button("Regenerate share link", key=f"regen_{rep_id}"):
                        try:
                            new_slug = make_public(rep_id, expires_in_days=30)
                            if new_slug:
                                st.success("Share link regenerated.")
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
