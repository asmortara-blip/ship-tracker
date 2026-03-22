"""
Investor Intelligence Report Tab — world-class institutional report generation UI.

Sections
--------
1. Bloomberg-style hero header
2. Report Configuration Panel
3. Generate Report button + progress
4. Report Preview Panel (post-generation)
5. Download buttons row
6. Report History
7. Data Source Status Panel
8. API Configuration (collapsed)

Function signature:
    render(port_results, route_results, insights, freight_data, macro_data, stock_data)
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import streamlit as st

try:
    from loguru import logger
except Exception:
    import logging
    logger = logging.getLogger(__name__)

# ── Colour palette ─────────────────────────────────────────────────────────────
C_BG      = "#0a0f1a"
C_SURFACE = "#111827"
C_CARD    = "#1a2235"
C_BORDER  = "rgba(255,255,255,0.08)"
C_HIGH    = "#10b981"
C_MOD     = "#f59e0b"
C_LOW     = "#ef4444"
C_ACCENT  = "#3b82f6"
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"

try:
    from ui.styles import apply_dark_layout
    _STYLES_OK = True
except Exception:
    _STYLES_OK = False
    def apply_dark_layout() -> None:
        pass

try:
    from processing.investor_report_engine import build_investor_report
    _ENGINE_OK = True
except Exception:
    _ENGINE_OK = False
    def build_investor_report(*args, **kwargs):
        raise ImportError("processing.investor_report_engine not available")

try:
    from utils.investor_report_pdf import render_investor_report_pdf, _FPDF_OK
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
        list_reports as _list_reports,
        load_report_html as _load_report_html,
        delete_report as _delete_report,
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
        "Baltic Exchange": ("BALTIC_API_KEY",),
        "Clarksons":       ("CLARKSONS_API_KEY",),
        "Bloomberg":       ("BLOOMBERG_API_KEY",),
        "Alpha Vantage":   ("ALPHA_VANTAGE_KEY",),
        "NewsAPI":         ("NEWSAPI_KEY",),
        "FRED / St. Louis":("FRED_API_KEY",),
        "IEX Cloud":       ("IEX_CLOUD_KEY",),
        "Quandl / Nasdaq": ("QUANDL_API_KEY", "NASDAQ_DATA_LINK_API_KEY"),
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


# ── Section renderers ──────────────────────────────────────────────────────────

def _render_hero(last_generated: str | None) -> None:
    ts_html = (
        f'<span style="color:{C_HIGH};font-weight:600;">{last_generated}</span>'
        if last_generated
        else f'<span style="color:{C_TEXT3};">Not yet generated this session</span>'
    )
    st.markdown(
        f"""
        <div style="background:linear-gradient(135deg,#0a1628 0%,#0f2044 50%,#0a1628 100%);
                    border:1px solid {C_BORDER};border-radius:16px;padding:36px 40px 28px;
                    margin-bottom:28px;position:relative;overflow:hidden;">
          <div style="position:absolute;top:0;left:0;right:0;height:3px;
                      background:linear-gradient(90deg,{C_ACCENT},{C_HIGH},{C_ACCENT});"></div>
          <div style="display:flex;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;gap:16px;">
            <div>
              <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">
                <span style="background:{C_ACCENT};color:#fff;font-size:10px;font-weight:800;
                             letter-spacing:2px;padding:3px 10px;border-radius:4px;">INSTITUTIONAL GRADE</span>
                <span style="background:rgba(16,185,129,0.15);color:{C_HIGH};font-size:10px;
                             font-weight:700;letter-spacing:1.5px;padding:3px 10px;border-radius:4px;
                             border:1px solid rgba(16,185,129,0.3);">MULTI-FACTOR ANALYSIS</span>
              </div>
              <div style="font-size:30px;font-weight:900;letter-spacing:3px;color:{C_TEXT};
                          font-family:monospace;line-height:1.1;">INVESTOR REPORT GENERATOR</div>
              <div style="font-size:13px;color:{C_TEXT2};margin-top:6px;letter-spacing:1px;">
                Global Shipping Market Intelligence &nbsp;|&nbsp; Quantitative &amp; Sentiment Driven
              </div>
            </div>
            <div style="text-align:right;">
              <div style="font-size:10px;color:{C_TEXT3};letter-spacing:1px;margin-bottom:4px;">LAST GENERATED</div>
              <div style="font-size:13px;">{ts_html}</div>
              <div style="font-size:10px;color:{C_TEXT3};margin-top:8px;">
                PDF &bull; HTML &bull; Excel &bull; JSON
              </div>
            </div>
          </div>
          <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:24px;">
            <div style="background:rgba(59,130,246,0.08);border:1px solid rgba(59,130,246,0.2);
                        border-radius:8px;padding:12px 16px;">
              <div style="font-size:10px;color:{C_TEXT3};letter-spacing:1px;">REPORT TYPE</div>
              <div style="font-size:14px;font-weight:700;color:{C_ACCENT};margin-top:2px;">Institutional</div>
            </div>
            <div style="background:rgba(16,185,129,0.08);border:1px solid rgba(16,185,129,0.2);
                        border-radius:8px;padding:12px 16px;">
              <div style="font-size:10px;color:{C_TEXT3};letter-spacing:1px;">SECTIONS</div>
              <div style="font-size:14px;font-weight:700;color:{C_HIGH};margin-top:2px;">10 Modules</div>
            </div>
            <div style="background:rgba(245,158,11,0.08);border:1px solid rgba(245,158,11,0.2);
                        border-radius:8px;padding:12px 16px;">
              <div style="font-size:10px;color:{C_TEXT3};letter-spacing:1px;">FORMATS</div>
              <div style="font-size:14px;font-weight:700;color:{C_MOD};margin-top:2px;">PDF / HTML / XLS</div>
            </div>
            <div style="background:rgba(139,92,246,0.08);border:1px solid rgba(139,92,246,0.2);
                        border-radius:8px;padding:12px 16px;">
              <div style="font-size:10px;color:{C_TEXT3};letter-spacing:1px;">ENGINE</div>
              <div style="font-size:14px;font-weight:700;color:#8b5cf6;margin-top:2px;">
                {"READY" if _ENGINE_OK else "OFFLINE"}
              </div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_config_panel(api_status: dict[str, bool]) -> dict:
    """Render configuration panel. Returns config dict."""
    live_count = _data_live_count(api_status)
    total = len(api_status)
    quality_color = C_HIGH if live_count >= 6 else (C_MOD if live_count >= 3 else C_LOW)
    quality_label = "LIVE" if live_count >= 6 else ("PARTIAL" if live_count >= 3 else "MOCK")

    st.markdown(
        f"""
        <div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;
                    padding:24px 28px 8px;margin-bottom:20px;">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">
            <div style="font-size:13px;font-weight:700;letter-spacing:2px;color:{C_TEXT};">
              REPORT CONFIGURATION
            </div>
            <div style="display:flex;align-items:center;gap:8px;">
              <div style="width:8px;height:8px;border-radius:50%;background:{quality_color};
                          box-shadow:0 0 6px {quality_color};"></div>
              <span style="font-size:11px;color:{quality_color};font-weight:700;letter-spacing:1px;">
                {quality_label} — {live_count}/{total} sources
              </span>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col_left, col_right = st.columns([1, 1], gap="large")
    config: dict[str, Any] = {}

    with col_left:
        st.markdown(f'<div style="font-size:11px;color:{C_TEXT3};letter-spacing:1px;margin-bottom:6px;">REPORT SCOPE</div>', unsafe_allow_html=True)
        scope = st.radio(
            "scope",
            ["Full Report", "Quick Digest", "Signal Focus", "Freight Focus"],
            label_visibility="collapsed",
            key="rep_scope",
        )
        config["scope"] = scope

        st.markdown(f'<div style="font-size:11px;color:{C_TEXT3};letter-spacing:1px;margin:12px 0 6px;">NARRATIVE TONE</div>', unsafe_allow_html=True)
        tone = st.radio(
            "tone",
            ["Formal", "Analytical", "Summary"],
            label_visibility="collapsed",
            key="rep_tone",
        )
        config["tone"] = tone

    with col_right:
        st.markdown(f'<div style="font-size:11px;color:{C_TEXT3};letter-spacing:1px;margin-bottom:6px;">INCLUDE SECTIONS</div>', unsafe_allow_html=True)
        sections = {}
        section_defs = [
            ("exec_summary",   "Executive Summary"),
            ("signals",        "Alpha Signals"),
            ("freight_rates",  "Freight Rates"),
            ("macro",          "Macro Environment"),
            ("equity",         "Equity Analysis"),
            ("risk",           "Risk Assessment"),
            ("recommendations","Recommendations"),
        ]
        for key, label in section_defs:
            sections[key] = st.checkbox(label, value=True, key=f"rep_sec_{key}")
        config["sections"] = sections

    st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
    return config


def _render_generate_button(
    config: dict,
    port_results, route_results, insights, freight_data, macro_data, stock_data,
) -> None:
    st.markdown(
        f"""
        <div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;
                    padding:24px 28px;margin-bottom:20px;">
          <div style="font-size:13px;font-weight:700;letter-spacing:2px;color:{C_TEXT};margin-bottom:4px;">
            GENERATE REPORT
          </div>
          <div style="font-size:12px;color:{C_TEXT3};margin-bottom:16px;">
            Runs the full multi-factor analysis pipeline and builds the institutional briefing.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if not _ENGINE_OK:
        st.markdown(
            f'<div style="background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);'
            f'border-radius:8px;padding:14px 18px;color:{C_LOW};font-size:13px;margin-bottom:16px;">'
            f'<strong>Engine unavailable.</strong> The report engine could not be loaded. '
            f'Check that <code>processing.investor_report_engine</code> is installed.</div>',
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
                    f'<div style="text-align:center;color:{C_TEXT2};font-size:13px;padding:8px;">'
                    f'<span style="color:{C_ACCENT};font-weight:700;">&#9654;</span> {step}</div>',
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
                f'<div style="background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.35);'
                f'border-radius:8px;padding:16px 20px;margin-top:12px;">'
                f'<div style="color:{C_LOW};font-weight:700;font-size:13px;margin-bottom:4px;">Generation Failed</div>'
                f'<div style="color:{C_TEXT2};font-size:12px;">{exc}</div></div>',
                unsafe_allow_html=True,
            )


def _render_report_preview(report: Any, ts: str) -> None:
    if report is None:
        st.markdown(
            f'<div style="background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.25);'
            f'border-radius:8px;padding:16px 20px;color:{C_LOW};font-size:13px;">'
            f'<strong>No report data.</strong> The report object is None — generation may have failed silently.</div>',
            unsafe_allow_html=True,
        )
        return

    # Extract key metrics safely
    try:
        sentiment_label = report.get("overall_sentiment_label", "N/A") if isinstance(report, dict) else getattr(report, "overall_sentiment_label", "N/A")
        sentiment_score = report.get("overall_sentiment_score", 0.0) if isinstance(report, dict) else getattr(report, "overall_sentiment_score", 0.0)
        risk_level      = report.get("risk_level", "N/A") if isinstance(report, dict) else getattr(report, "risk_level", "N/A")
        top_pick        = report.get("top_pick", "N/A") if isinstance(report, dict) else getattr(report, "top_pick", "N/A")
        data_quality    = report.get("data_quality", "N/A") if isinstance(report, dict) else getattr(report, "data_quality", "N/A")
        signal_count    = len(report.get("signals", [])) if isinstance(report, dict) else len(getattr(report, "signals", []) or [])
        exec_summary    = (report.get("executive_summary", "") if isinstance(report, dict) else getattr(report, "executive_summary", "")) or ""
    except Exception as exc:
        logger.warning(f"Could not parse report fields: {exc}")
        sentiment_label = sentiment_score = risk_level = top_pick = data_quality = "N/A"
        signal_count = 0
        exec_summary = ""

    score_color = _score_color(float(sentiment_score) if str(sentiment_score).replace(".", "", 1).lstrip("-").isdigit() else 0.5)
    risk_col = _risk_color(str(risk_level))
    score_pct = f"{float(sentiment_score):.2f}" if str(sentiment_score).replace(".", "", 1).lstrip("-").isdigit() else "—"

    st.markdown(
        f"""
        <div style="background:linear-gradient(135deg,rgba(16,185,129,0.08),rgba(59,130,246,0.04));
                    border:1px solid rgba(16,185,129,0.3);border-radius:12px;padding:20px 24px;margin-bottom:20px;">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:16px;">
            <div style="width:10px;height:10px;border-radius:50%;background:{C_HIGH};box-shadow:0 0 8px {C_HIGH};"></div>
            <span style="font-size:13px;font-weight:800;letter-spacing:2px;color:{C_HIGH};">REPORT GENERATED</span>
            <span style="font-size:11px;color:{C_TEXT3};margin-left:auto;">{ts}</span>
          </div>
          <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:16px;">
            <div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:8px;padding:12px 14px;">
              <div style="font-size:9px;color:{C_TEXT3};letter-spacing:1px;">SENTIMENT</div>
              <div style="font-size:16px;font-weight:800;color:{score_color};margin-top:2px;">{sentiment_label}</div>
              <div style="font-size:11px;color:{C_TEXT3};">{score_pct}</div>
            </div>
            <div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:8px;padding:12px 14px;">
              <div style="font-size:9px;color:{C_TEXT3};letter-spacing:1px;">SIGNALS</div>
              <div style="font-size:16px;font-weight:800;color:{C_ACCENT};margin-top:2px;">{signal_count}</div>
              <div style="font-size:11px;color:{C_TEXT3};">detected</div>
            </div>
            <div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:8px;padding:12px 14px;">
              <div style="font-size:9px;color:{C_TEXT3};letter-spacing:1px;">RISK LEVEL</div>
              <div style="font-size:16px;font-weight:800;color:{risk_col};margin-top:2px;">{risk_level}</div>
            </div>
            <div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:8px;padding:12px 14px;">
              <div style="font-size:9px;color:{C_TEXT3};letter-spacing:1px;">TOP PICK</div>
              <div style="font-size:14px;font-weight:800;color:{C_HIGH};margin-top:2px;">{top_pick}</div>
            </div>
            <div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:8px;padding:12px 14px;">
              <div style="font-size:9px;color:{C_TEXT3};letter-spacing:1px;">DATA QUALITY</div>
              <div style="font-size:14px;font-weight:800;color:{C_MOD};margin-top:2px;">{data_quality}</div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Section pills
    section_names = [
        "Cover Page", "Executive Summary", "Alpha Signals", "Freight Rates",
        "Macro Environment", "Equity Analysis", "Risk Assessment",
        "Recommendations", "Data Appendix", "Methodology",
    ]
    pills_html = "".join(
        f'<span style="background:rgba(59,130,246,0.12);border:1px solid rgba(59,130,246,0.25);'
        f'color:{C_TEXT2};font-size:11px;padding:4px 10px;border-radius:20px;">{s}</span>'
        for s in section_names
    )
    st.markdown(
        f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;'
        f'padding:16px 20px;margin-bottom:20px;">'
        f'<div style="font-size:10px;color:{C_TEXT3};letter-spacing:1px;margin-bottom:10px;">REPORT SECTIONS</div>'
        f'<div style="display:flex;flex-wrap:wrap;gap:6px;">{pills_html}</div></div>',
        unsafe_allow_html=True,
    )

    # Executive summary preview
    if exec_summary:
        preview = exec_summary[:300] + ("…" if len(exec_summary) > 300 else "")
        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;'
            f'padding:18px 22px;margin-bottom:20px;">'
            f'<div style="font-size:10px;color:{C_TEXT3};letter-spacing:1px;margin-bottom:10px;">EXECUTIVE SUMMARY — PREVIEW</div>'
            f'<div style="font-size:13px;color:{C_TEXT};line-height:1.7;font-style:italic;">{preview}</div></div>',
            unsafe_allow_html=True,
        )


def _render_downloads(report: Any) -> None:
    st.markdown(
        f'<div style="font-size:12px;font-weight:700;letter-spacing:2px;color:{C_TEXT};'
        f'margin-bottom:12px;">DOWNLOAD REPORT</div>',
        unsafe_allow_html=True,
    )

    col_pdf, col_html, col_xl = st.columns([2, 1, 1], gap="medium")

    # PDF
    with col_pdf:
        if _PDF_OK and report is not None:
            try:
                pdf_bytes = render_investor_report_pdf(report)
                fname = f"investor_report_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
                size_kb = len(pdf_bytes) // 1024
                st.download_button(
                    label=f"Download PDF Report  ({size_kb} KB)",
                    data=pdf_bytes,
                    file_name=fname,
                    mime="application/pdf",
                    key="dl_pdf",
                    type="primary",
                    use_container_width=True,
                )
            except Exception as exc:
                logger.error(f"PDF render failed: {exc}")
                st.markdown(
                    f'<div style="background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);'
                    f'border-radius:8px;padding:12px 16px;color:{C_LOW};font-size:12px;">'
                    f'<strong>PDF generation failed:</strong> {exc}</div>',
                    unsafe_allow_html=True,
                )
        elif not _PDF_OK:
            st.markdown(
                f'<div style="background:rgba(100,116,139,0.1);border:1px solid rgba(100,116,139,0.2);'
                f'border-radius:8px;padding:12px 16px;color:{C_TEXT3};font-size:12px;">'
                f'PDF unavailable — install <code>fpdf2</code> and check <code>utils.investor_report_pdf</code>.</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div style="color:{C_TEXT3};font-size:12px;padding:12px;">Generate a report first.</div>',
                unsafe_allow_html=True,
            )

    # HTML
    with col_html:
        if _HTML_OK and report is not None:
            try:
                html_str = render_investor_report_html(report)
                html_bytes = html_str.encode("utf-8")
                fname_h = f"investor_report_{datetime.now().strftime('%Y%m%d_%H%M')}.html"
                size_kb = len(html_bytes) // 1024
                st.download_button(
                    label=f"Download HTML ({size_kb} KB)",
                    data=html_bytes,
                    file_name=fname_h,
                    mime="text/html",
                    key="dl_html",
                    use_container_width=True,
                )
            except Exception as exc:
                logger.error(f"HTML render failed: {exc}")
                st.markdown(
                    f'<div style="color:{C_LOW};font-size:11px;padding:4px;">HTML error: {exc}</div>',
                    unsafe_allow_html=True,
                )
        elif not _HTML_OK:
            st.markdown(
                f'<div style="color:{C_TEXT3};font-size:11px;padding:4px;">HTML renderer unavailable.</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div style="color:{C_TEXT3};font-size:11px;padding:4px;">Generate a report first.</div>',
                unsafe_allow_html=True,
            )

    # Excel
    with col_xl:
        if _EXCEL_OK and report is not None:
            try:
                xl_bytes = _export_full_report(report)
                fname_x = f"investor_report_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
                size_kb = len(xl_bytes) // 1024
                st.download_button(
                    label=f"Download Excel ({size_kb} KB)",
                    data=xl_bytes,
                    file_name=fname_x,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="dl_excel",
                    use_container_width=True,
                )
            except Exception as exc:
                logger.error(f"Excel export failed: {exc}")
                st.markdown(
                    f'<div style="color:{C_LOW};font-size:11px;padding:4px;">Excel error: {exc}</div>',
                    unsafe_allow_html=True,
                )
        elif not _EXCEL_OK:
            st.markdown(
                f'<div style="color:{C_TEXT3};font-size:11px;padding:4px;">Excel export unavailable.</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div style="color:{C_TEXT3};font-size:11px;padding:4px;">Generate a report first.</div>',
                unsafe_allow_html=True,
            )


def _render_history() -> None:
    st.markdown(
        f'<div style="font-size:12px;font-weight:700;letter-spacing:2px;color:{C_TEXT};'
        f'margin:28px 0 12px;">REPORT HISTORY</div>',
        unsafe_allow_html=True,
    )

    if not _HISTORY_OK:
        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:8px;'
            f'padding:14px 18px;color:{C_TEXT3};font-size:12px;">'
            f'Report history unavailable — <code>utils.report_history</code> not loaded.</div>',
            unsafe_allow_html=True,
        )
        return

    try:
        reports = _list_reports()
    except Exception as exc:
        logger.warning(f"Could not list reports: {exc}")
        reports = []

    if not reports:
        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:8px;'
            f'padding:14px 18px;color:{C_TEXT3};font-size:12px;">No historical reports saved yet.</div>',
            unsafe_allow_html=True,
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
                st.markdown(
                    f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:8px;'
                    f'padding:12px 16px;display:flex;align-items:center;gap:16px;">'
                    f'<span style="font-size:12px;color:{C_TEXT2};font-family:monospace;">{rep_date}</span>'
                    f'<span style="font-size:11px;font-weight:700;color:{sent_color};">{rep_sent}</span>'
                    f'<span style="font-size:11px;color:{C_TEXT3};">Quality: {rep_qual}</span>'
                    f'<span style="font-size:11px;color:{C_TEXT3};margin-left:auto;">{rep_size} KB</span>'
                    f'</div>',
                    unsafe_allow_html=True,
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
                        st.markdown(f'<div style="color:{C_TEXT3};font-size:11px;padding:8px;">Unavailable</div>', unsafe_allow_html=True)
                except Exception:
                    st.markdown(f'<div style="color:{C_TEXT3};font-size:11px;padding:8px;">—</div>', unsafe_allow_html=True)
        except Exception as exc:
            logger.warning(f"Could not render history row {i}: {exc}")


def _render_data_sources(api_status: dict[str, bool]) -> None:
    live_count = _data_live_count(api_status)
    total = len(api_status)
    summary_color = C_HIGH if live_count >= 6 else (C_MOD if live_count >= 3 else C_LOW)

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

    st.markdown(
        f"""
        <div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;
                    padding:22px 26px;margin-top:28px;">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">
            <div style="font-size:12px;font-weight:700;letter-spacing:2px;color:{C_TEXT};">
              DATA SOURCE STATUS
            </div>
            <span style="font-size:12px;font-weight:700;color:{summary_color};">
              {live_count} of {total} sources live
            </span>
          </div>
          <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;">
            {items_html}
          </div>
          <div style="margin-top:14px;font-size:11px;color:{C_TEXT3};">
            For full diagnostics, visit the <strong style="color:{C_ACCENT};">Data Health</strong> tab.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_api_config(api_status: dict[str, bool]) -> None:
    with st.expander("API Configuration", expanded=False):
        st.markdown(
            f'<div style="font-size:12px;color:{C_TEXT2};margin-bottom:14px;line-height:1.6;">'
            f'Configure API keys via <code>st.secrets</code> (secrets.toml) or environment variables. '
            f'Keys are never displayed — only their presence is checked.</div>',
            unsafe_allow_html=True,
        )
        key_map = {
            "Baltic Exchange":   "BALTIC_API_KEY",
            "Clarksons":         "CLARKSONS_API_KEY",
            "Bloomberg":         "BLOOMBERG_API_KEY",
            "Alpha Vantage":     "ALPHA_VANTAGE_KEY",
            "NewsAPI":           "NEWSAPI_KEY",
            "FRED / St. Louis":  "FRED_API_KEY",
            "IEX Cloud":         "IEX_CLOUD_KEY",
            "Quandl / Nasdaq":   "QUANDL_API_KEY",
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
        st.markdown(
            f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:8px;'
            f'padding:12px 16px;">{rows_html}</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div style="margin-top:12px;font-size:11px;color:{C_TEXT3};line-height:1.7;">'
            f'Add keys to <code>.streamlit/secrets.toml</code>:<br>'
            f'<code>ALPHA_VANTAGE_KEY = "your-key-here"</code><br>'
            f'Or set environment variables before launching the app.</div>',
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
) -> None:
    """Render the Investor Report tab."""
    try:
        apply_dark_layout()
    except Exception:
        pass

    # Session state
    report     = st.session_state.get("investor_report")
    last_ts    = st.session_state.get("investor_report_ts")
    api_status = _check_api_keys()

    # 1 — Hero
    try:
        _render_hero(last_ts)
    except Exception as exc:
        logger.error(f"Hero render error: {exc}")
        st.error("Could not render header.")

    # 2 — Configuration
    config: dict = {}
    try:
        config = _render_config_panel(api_status)
    except Exception as exc:
        logger.error(f"Config panel error: {exc}")
        st.error("Could not render configuration panel.")

    st.markdown("<div style='height:4px;'></div>", unsafe_allow_html=True)

    # 3 — Generate button
    try:
        _render_generate_button(config, port_results, route_results, insights, freight_data, macro_data, stock_data)
    except Exception as exc:
        logger.error(f"Generate button error: {exc}")
        st.error("Could not render generate button.")

    # 4 — Report preview (only if report exists)
    if report is not None:
        st.markdown("<hr style='border:none;border-top:1px solid rgba(255,255,255,0.06);margin:24px 0;'>", unsafe_allow_html=True)
        try:
            _render_report_preview(report, last_ts or _now_utc())
        except Exception as exc:
            logger.error(f"Report preview error: {exc}")
            st.error("Could not render report preview.")

        # 5 — Downloads
        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;'
            f'padding:22px 26px;margin-bottom:20px;">',
            unsafe_allow_html=True,
        )
        try:
            _render_downloads(report)
        except Exception as exc:
            logger.error(f"Download section error: {exc}")
            st.error("Could not render download buttons.")
        st.markdown("</div>", unsafe_allow_html=True)

    elif report is None and last_ts is not None:
        # Report was generated but came back None
        st.markdown(
            f'<div style="background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.25);'
            f'border-radius:8px;padding:16px 20px;color:{C_LOW};font-size:13px;margin-top:16px;">'
            f'<strong>Report data is None.</strong> The engine ran but returned no data. '
            f'Check logs for details.</div>',
            unsafe_allow_html=True,
        )
    else:
        # Not yet generated — show download placeholders
        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;'
            f'padding:22px 26px;margin-top:8px;margin-bottom:20px;">',
            unsafe_allow_html=True,
        )
        try:
            _render_downloads(None)
        except Exception as exc:
            logger.error(f"Download placeholder error: {exc}")
        st.markdown("</div>", unsafe_allow_html=True)

    # 6 — Report History
    st.markdown("<hr style='border:none;border-top:1px solid rgba(255,255,255,0.06);margin:24px 0;'>", unsafe_allow_html=True)
    try:
        _render_history()
    except Exception as exc:
        logger.error(f"History render error: {exc}")
        st.error("Could not render report history.")

    # 7 — Data Source Status
    try:
        _render_data_sources(api_status)
    except Exception as exc:
        logger.error(f"Data source status error: {exc}")
        st.error("Could not render data source status.")

    # 8 — API Configuration
    st.markdown("<div style='height:16px;'></div>", unsafe_allow_html=True)
    try:
        _render_api_config(api_status)
    except Exception as exc:
        logger.error(f"API config render error: {exc}")
        st.error("Could not render API configuration.")
