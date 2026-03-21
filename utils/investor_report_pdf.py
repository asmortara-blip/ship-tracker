"""investor_report_pdf.py — PDF generator for InvestorReport using fpdf2.

Produces a fully self-contained, multi-page PDF from an InvestorReport object.
Uses only the built-in Helvetica font — no external font files required.
Designed for Streamlit Cloud (no system dependencies beyond fpdf2).

Usage:
    from utils.investor_report_pdf import render_investor_report_pdf
    pdf_bytes = render_investor_report_pdf(report)
    st.download_button("Download PDF", pdf_bytes, "report.pdf", "application/pdf")
"""
from __future__ import annotations

import io
import traceback
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional, Tuple

try:
    from fpdf import FPDF
    _FPDF_OK = True
except ImportError:
    _FPDF_OK = False

if TYPE_CHECKING:
    from processing.investor_report_engine import InvestorReport


# ---------------------------------------------------------------------------
# Color palette (RGB tuples — mirrors ui/styles.py)
# ---------------------------------------------------------------------------

C_BG      = (10,  15,  26)    # #0a0f1a — page background
C_SURFACE = (17,  24,  39)    # #111827 — surface
C_CARD    = (26,  34,  53)    # #1a2235 — card background
C_HIGH    = (16,  185, 129)   # #10b981 — green / bullish
C_MOD     = (245, 158, 11)    # #f59e0b — amber / neutral
C_LOW     = (239, 68,  68)    # #ef4444 — red / bearish
C_ACCENT  = (59,  130, 246)   # #3b82f6 — blue / primary accent
C_CONV    = (139, 92,  246)   # #8b5cf6 — purple / convergence
C_TEXT    = (241, 245, 249)   # #f1f5f9 — primary text
C_TEXT2   = (148, 163, 184)   # #94a3b8 — secondary text
C_TEXT3   = (100, 116, 139)   # #64748b — tertiary text
C_WHITE   = (255, 255, 255)

# Section header bar colors
_SECTION_COLORS = {
    "executive":  C_ACCENT,
    "sentiment":  C_CONV,
    "alpha":      C_HIGH,
    "market":     C_MOD,
    "freight":    C_ACCENT,
    "stocks":     C_HIGH,
    "disclaimer": C_TEXT3,
}

_RISK_COLORS = {
    "LOW":      C_HIGH,
    "MODERATE": C_MOD,
    "HIGH":     C_LOW,
    "CRITICAL": (185, 28, 28),
}

_SENTIMENT_COLORS = {
    "BULLISH": C_HIGH,
    "BEARISH": C_LOW,
    "NEUTRAL": C_TEXT2,
    "MIXED":   C_MOD,
}

_CONVICTION_COLORS = {
    "HIGH":   C_HIGH,
    "MEDIUM": C_MOD,
    "LOW":    C_TEXT3,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe(val, default="N/A"):
    """Return val as string, or default if None / empty."""
    if val is None:
        return str(default)
    s = str(val).strip()
    return s if s else str(default)


def _fmt_float(val, decimals: int = 2, prefix: str = "", suffix: str = "") -> str:
    try:
        f = float(val)
        sign = "+" if f > 0 else ""
        return f"{prefix}{sign}{f:.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_price(val) -> str:
    try:
        return f"${float(val):,.2f}"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_int(val) -> str:
    try:
        return str(int(val))
    except (TypeError, ValueError):
        return "N/A"


def _clamp(val: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, val))


def _score_color(score: float) -> Tuple[int, int, int]:
    """Map a [0,1] score to a color."""
    if score >= 0.70:
        return C_HIGH
    if score >= 0.40:
        return C_MOD
    return C_LOW


def _split_paragraphs(text: str, max_len: int = 800) -> List[str]:
    """Split a long string on double-newline into paragraphs."""
    if not text:
        return []
    parts = [p.strip() for p in text.split("\n\n") if p.strip()]
    result = []
    for part in parts:
        if len(part) > max_len:
            # Hard-split on sentence boundaries
            sentences = part.split(". ")
            chunk = ""
            for s in sentences:
                if len(chunk) + len(s) < max_len:
                    chunk += s + ". "
                else:
                    if chunk:
                        result.append(chunk.strip())
                    chunk = s + ". "
            if chunk:
                result.append(chunk.strip())
        else:
            result.append(part)
    return result


# ---------------------------------------------------------------------------
# PDF class
# ---------------------------------------------------------------------------

class InvestorReportPDF(FPDF):
    """Custom FPDF subclass with Ship Tracker styling."""

    # Page size: Letter (215.9 × 279.4 mm)
    PAGE_W = 215.9
    PAGE_H = 279.4
    MARGIN = 14.0
    INNER_W = PAGE_W - 2 * 14.0   # 187.9 mm

    def __init__(self):
        super().__init__(orientation="P", unit="mm", format="Letter")
        self.set_margins(self.MARGIN, self.MARGIN, self.MARGIN)
        self.set_auto_page_break(auto=True, margin=18.0)
        self._total_pages: int = 0   # filled in after generation

    # ------------------------------------------------------------------
    # Header / Footer
    # ------------------------------------------------------------------

    def header(self):
        if self.page_no() == 1:
            return  # Cover page has its own full-bleed design

        # Background strip
        self.set_fill_color(*C_SURFACE)
        self.rect(0, 0, self.PAGE_W, 10, "F")

        # Left: platform name
        self.set_xy(self.MARGIN, 2.5)
        self.set_font("Helvetica", "B", 7)
        self.set_text_color(*C_ACCENT)
        self.cell(0, 5, "GLOBAL SHIPPING INTELLIGENCE", align="L")

        # Right: page number
        self.set_xy(-self.MARGIN - 30, 2.5)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*C_TEXT3)
        self.cell(30, 5, f"Page {self.page_no()}", align="R")

        # Thin accent line
        self.set_draw_color(*C_ACCENT)
        self.set_line_width(0.3)
        self.line(self.MARGIN, 10, self.PAGE_W - self.MARGIN, 10)

    def footer(self):
        if self.page_no() == 1:
            return  # Cover has its own footer

        # Thin line above footer
        self.set_draw_color(*C_TEXT3)
        self.set_line_width(0.2)
        self.line(self.MARGIN, self.PAGE_H - 10, self.PAGE_W - self.MARGIN, self.PAGE_H - 10)

        self.set_xy(self.MARGIN, self.PAGE_H - 9)
        self.set_font("Helvetica", "I", 6)
        self.set_text_color(*C_TEXT3)
        self.cell(0, 4, "CONFIDENTIAL — FOR INSTITUTIONAL USE ONLY", align="C")

    # ------------------------------------------------------------------
    # Helper: section header bar
    # ------------------------------------------------------------------

    def _section_header(self, title: str, color: tuple) -> None:
        """Colored full-width rectangle with white title text."""
        self.set_fill_color(*color)
        y = self.get_y()
        self.rect(self.MARGIN, y, self.INNER_W, 8, "F")
        self.set_xy(self.MARGIN + 3, y + 1.5)
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*C_WHITE)
        self.cell(self.INNER_W - 6, 5, title.upper(), align="L")
        self.ln(10)

    # ------------------------------------------------------------------
    # Helper: KPI box
    # ------------------------------------------------------------------

    def _kpi_box(self, x: float, y: float, w: float, h: float,
                 label: str, value: str, sub: str, color: tuple) -> None:
        """Draw a metric card: colored top border + label / value / sub."""
        # Card background
        self.set_fill_color(*C_CARD)
        self.rect(x, y, w, h, "F")
        # Top accent bar
        self.set_fill_color(*color)
        self.rect(x, y, w, 1.2, "F")
        # Label
        self.set_xy(x + 2, y + 2.5)
        self.set_font("Helvetica", "", 6)
        self.set_text_color(*C_TEXT3)
        self.cell(w - 4, 4, label.upper(), align="L")
        # Value
        self.set_xy(x + 2, y + 7)
        self.set_font("Helvetica", "B", 12)
        self.set_text_color(*C_TEXT)
        self.cell(w - 4, 7, str(value), align="L")
        # Sub-label
        if sub:
            self.set_xy(x + 2, y + 14.5)
            self.set_font("Helvetica", "I", 6)
            self.set_text_color(*C_TEXT2)
            self.cell(w - 4, 4, str(sub), align="L")

    # ------------------------------------------------------------------
    # Helper: horizontal progress bar
    # ------------------------------------------------------------------

    def _horizontal_bar(self, x: float, y: float, w: float, h: float,
                        value: float, max_val: float, color: tuple) -> None:
        """Draw a filled progress bar (track + fill)."""
        # Track
        self.set_fill_color(*C_SURFACE)
        self.rect(x, y, w, h, "F")
        # Fill
        ratio = _clamp(value / max_val if max_val else 0.0)
        if ratio > 0:
            self.set_fill_color(*color)
            self.rect(x, y, w * ratio, h, "F")

    # ------------------------------------------------------------------
    # Helper: colored badge pill
    # ------------------------------------------------------------------

    def _badge(self, x: float, y: float, text: str, bg: tuple,
               fg: tuple = None) -> None:
        """Draw a colored pill badge (rectangle + text)."""
        fg = fg or C_WHITE
        text_w = len(text) * 1.8 + 4   # rough estimate
        self.set_fill_color(*bg)
        self.rect(x, y, text_w, 5, "F")
        self.set_xy(x + 2, y + 0.5)
        self.set_font("Helvetica", "B", 6)
        self.set_text_color(*fg)
        self.cell(text_w - 4, 4, text.upper(), align="C")

    # ------------------------------------------------------------------
    # Helper: table renderer
    # ------------------------------------------------------------------

    def _row_table(self, headers: List[str], rows: List[List[str]],
                   col_widths: List[float], start_y: float = None) -> float:
        """Draw a table with header row and data rows. Returns ending Y."""
        if start_y is not None:
            self.set_y(start_y)

        # Header row
        self.set_fill_color(*C_SURFACE)
        self.set_font("Helvetica", "B", 7)
        self.set_text_color(*C_ACCENT)
        x_start = self.MARGIN
        y = self.get_y()
        self.rect(x_start, y, self.INNER_W, 6, "F")
        x = x_start
        for hdr, cw in zip(headers, col_widths):
            self.set_xy(x + 1, y + 1)
            self.cell(cw - 2, 4, str(hdr).upper(), align="L")
            x += cw
        self.ln(6)

        # Data rows
        for i, row in enumerate(rows):
            y = self.get_y()
            if self.will_page_break(6):
                self.add_page()
                y = self.get_y()

            if i % 2 == 1:
                self.set_fill_color(20, 28, 46)
                self.rect(x_start, y, self.INNER_W, 6, "F")

            self.set_font("Helvetica", "", 7)
            self.set_text_color(*C_TEXT2)
            x = x_start
            for j, (cell_val, cw) in enumerate(zip(row, col_widths)):
                self.set_xy(x + 1, y + 1)
                # First column: slightly brighter
                if j == 0:
                    self.set_font("Helvetica", "B", 7)
                    self.set_text_color(*C_TEXT)
                else:
                    self.set_font("Helvetica", "", 7)
                    self.set_text_color(*C_TEXT2)
                txt = str(cell_val)
                self.cell(cw - 2, 4, txt[:32], align="L")
                x += cw
            self.ln(6)

        return self.get_y()

    # ------------------------------------------------------------------
    # Helper: colored text line
    # ------------------------------------------------------------------

    def _colored_text(self, text: str, color: tuple) -> None:
        """Set text color and write a line."""
        self.set_text_color(*color)
        self.write(5, str(text))

    # ------------------------------------------------------------------
    # Helper: dark-bg multi_cell wrapper
    # ------------------------------------------------------------------

    def _prose(self, text: str, size: int = 8, color: tuple = None,
               indent: float = 0) -> None:
        """Write a block of prose text on the dark background."""
        color = color or C_TEXT2
        self.set_font("Helvetica", "", size)
        self.set_text_color(*color)
        x = self.MARGIN + indent
        self.set_x(x)
        self.multi_cell(self.INNER_W - indent, 5, str(text), align="J")
        self.ln(2)

    # ------------------------------------------------------------------
    # Helper: small spacer
    # ------------------------------------------------------------------

    def _gap(self, h: float = 3.0) -> None:
        self.ln(h)

    # ------------------------------------------------------------------
    # Helper: fill page with background color
    # ------------------------------------------------------------------

    def _fill_page_bg(self, color: tuple = None) -> None:
        color = color or C_BG
        self.set_fill_color(*color)
        self.rect(0, 0, self.PAGE_W, self.PAGE_H, "F")


# ---------------------------------------------------------------------------
# Page builders
# ---------------------------------------------------------------------------

def _page_cover(pdf: InvestorReportPDF, report) -> None:
    """Page 1: Cover page with dark full-bleed design."""
    pdf.add_page()
    pdf._fill_page_bg()

    w = pdf.PAGE_W
    h = pdf.PAGE_H
    m = pdf.MARGIN

    # Accent gradient bars (decorative horizontal bars)
    pdf.set_fill_color(*C_ACCENT)
    pdf.rect(0, 0, w, 1.5, "F")
    pdf.set_fill_color(*C_CONV)
    pdf.rect(0, 1.5, w, 0.8, "F")

    # Top eyebrow text
    pdf.set_xy(m, 14)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(*C_ACCENT)
    pdf.cell(0, 5, "SHIP TRACKER INTELLIGENCE PLATFORM  |  INSTITUTIONAL RESEARCH", align="L")

    # Main title
    pdf.set_xy(m, 28)
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(*C_WHITE)
    pdf.multi_cell(w - 2 * m, 12, "GLOBAL SHIPPING\nINTELLIGENCE REPORT", align="L")

    # Subtitle
    pdf.set_x(m)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*C_TEXT2)
    pdf.multi_cell(w - 2 * m, 6,
                   "Institutional Sentiment Analysis & Alpha Signal Briefing", align="L")
    pdf.ln(3)

    # Report date
    pdf.set_x(m)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(*C_TEXT3)
    pdf.cell(0, 5, f"Report Date: {_safe(report.report_date)}  |  Generated: {_safe(report.generated_at)}")
    pdf.ln(10)

    # Divider line
    pdf.set_draw_color(*C_ACCENT)
    pdf.set_line_width(0.5)
    pdf.line(m, pdf.get_y(), w - m, pdf.get_y())
    pdf.ln(8)

    # Sentiment badge (large colored rectangle)
    sent_label = _safe(report.sentiment.overall_label, "NEUTRAL")
    sent_color = _SENTIMENT_COLORS.get(sent_label, C_TEXT2)
    sent_score = getattr(report.sentiment, "overall_score", 0.0)

    badge_x = m
    badge_y = pdf.get_y()
    badge_w = 50
    badge_h = 16
    pdf.set_fill_color(*sent_color)
    pdf.rect(badge_x, badge_y, badge_w, badge_h, "F")
    pdf.set_xy(badge_x, badge_y + 2)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(*C_WHITE)
    pdf.cell(badge_w, 7, sent_label, align="C")
    pdf.set_xy(badge_x, badge_y + 9)
    pdf.set_font("Helvetica", "", 7)
    pdf.cell(badge_w, 5, "MARKET SENTIMENT", align="C")

    # Score text beside badge
    pdf.set_xy(badge_x + badge_w + 6, badge_y + 2)
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(*sent_color)
    score_txt = f"{sent_score:+.2f}"
    pdf.cell(30, 10, score_txt, align="L")
    pdf.set_xy(badge_x + badge_w + 6, badge_y + 11)
    pdf.set_font("Helvetica", "", 7)
    pdf.set_text_color(*C_TEXT3)
    pdf.cell(30, 5, "COMPOSITE SCORE (-1 to +1)", align="L")

    pdf.set_y(badge_y + badge_h + 10)

    # 4 key stats in boxes
    risk_level = _safe(report.market.risk_level, "MODERATE")
    risk_color = _RISK_COLORS.get(risk_level, C_MOD)
    data_quality = _safe(report.data_quality, "PARTIAL")
    n_signals = _fmt_int(len(getattr(report.alpha, "signals", [])))
    n_high = _fmt_int(report.market.high_conviction_count)

    box_w = (pdf.INNER_W - 9) / 4
    box_h = 22
    box_y = pdf.get_y()
    boxes = [
        ("Sentiment Score", f"{sent_score:+.2f}", sent_label, sent_color),
        ("Alpha Signals",   n_signals,             "Active",          C_ACCENT),
        ("Risk Level",      risk_level,             "Assessment",      risk_color),
        ("Data Quality",    data_quality,           "Feed Status",     C_CONV),
    ]
    for i, (lbl, val, sub, col) in enumerate(boxes):
        bx = m + i * (box_w + 3)
        pdf._kpi_box(bx, box_y, box_w, box_h, lbl, val, sub, col)

    pdf.set_y(box_y + box_h + 12)

    # Brief description text
    pdf.set_x(m)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*C_TEXT2)
    pdf.multi_cell(pdf.INNER_W, 5,
                   "This report is generated by the Ship Tracker Intelligence Platform and is intended "
                   "for institutional investors and qualified market participants. It synthesises "
                   "multi-source freight, macro, news sentiment, and equity signals into a "
                   "structured investment briefing.", align="J")

    pdf.ln(8)

    # Bottom "CONFIDENTIAL" stamp
    pdf.set_fill_color(*C_SURFACE)
    pdf.rect(0, h - 18, w, 18, "F")
    pdf.set_draw_color(*C_ACCENT)
    pdf.set_line_width(0.4)
    pdf.line(0, h - 18, w, h - 18)
    pdf.set_xy(m, h - 14)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(*C_TEXT3)
    pdf.cell(w - 2 * m, 5,
             "CONFIDENTIAL — FOR INSTITUTIONAL USE ONLY — NOT FOR REDISTRIBUTION",
             align="C")
    pdf.set_xy(m, h - 9)
    pdf.set_font("Helvetica", "", 6)
    pdf.cell(w - 2 * m, 5,
             "This document does not constitute investment advice. See disclaimer on final page.",
             align="C")


def _page_executive_summary(pdf: InvestorReportPDF, report) -> None:
    """Page 2: Executive Summary."""
    pdf.add_page()

    # Section header
    pdf._section_header("02  |  Executive Summary", C_ACCENT)

    # 4 KPI boxes across the top
    ports_n   = _fmt_int(report.market.active_opportunities)
    routes_n  = _fmt_int(len(getattr(report.market, "top_routes", [])))
    signals_n = _fmt_int(len(getattr(report.alpha, "signals", [])))
    hiconv_n  = _fmt_int(report.market.high_conviction_count)

    box_w = (pdf.INNER_W - 9) / 4
    box_h = 20
    box_y = pdf.get_y()
    kpis = [
        ("Active Opportunities", ports_n,   "Prioritize/Monitor", C_HIGH),
        ("Routes Tracked",       routes_n,  "Top routes",          C_ACCENT),
        ("Active Signals",       signals_n, "Alpha engine",        C_CONV),
        ("High Conviction",      hiconv_n,  "Score ≥ 70%",         C_MOD),
    ]
    for i, (lbl, val, sub, col) in enumerate(kpis):
        bx = pdf.MARGIN + i * (box_w + 3)
        pdf._kpi_box(bx, box_y, box_w, box_h, lbl, val, sub, col)

    pdf.set_y(box_y + box_h + 8)
    pdf._gap(2)

    # Executive summary prose (3 paragraphs)
    exec_summary = getattr(report.ai, "executive_summary", "")
    paragraphs = _split_paragraphs(exec_summary)
    if not paragraphs:
        paragraphs = ["Market data is being aggregated. Please check back after a full data refresh."]

    for para in paragraphs[:3]:
        pdf._prose(para, size=8, color=C_TEXT2)
        pdf._gap(2)

    pdf._gap(3)

    # Top 3 key findings as bullet boxes
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*C_TEXT)
    pdf.cell(0, 5, "KEY FINDINGS", align="L")
    pdf.ln(6)

    top_insights = getattr(report.market, "top_insights", [])[:3]
    for ins in top_insights:
        title  = _safe(getattr(ins, "title", "Signal"))
        action = _safe(getattr(ins, "action", "Monitor"))
        score  = float(getattr(ins, "score", 0.0))
        detail = _safe(getattr(ins, "detail", ""))[:200]
        cat    = _safe(getattr(ins, "category", ""))
        s_col  = _score_color(score)

        fy = pdf.get_y()
        if pdf.will_page_break(16):
            pdf.add_page()
            fy = pdf.get_y()

        # Box border
        pdf.set_fill_color(*C_CARD)
        pdf.rect(pdf.MARGIN, fy, pdf.INNER_W, 16, "F")
        # Left accent
        pdf.set_fill_color(*s_col)
        pdf.rect(pdf.MARGIN, fy, 2, 16, "F")

        # Title + score
        pdf.set_xy(pdf.MARGIN + 5, fy + 2)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*C_TEXT)
        pdf.cell(pdf.INNER_W - 30, 5, title[:60], align="L")
        pdf.set_xy(pdf.PAGE_W - pdf.MARGIN - 25, fy + 2)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*s_col)
        pdf.cell(22, 5, f"{score * 100:.0f}%  {action.upper()}", align="R")

        # Category label
        pdf.set_xy(pdf.MARGIN + 5, fy + 7)
        pdf.set_font("Helvetica", "", 6.5)
        pdf.set_text_color(*C_TEXT3)
        pdf.cell(40, 4, f"{cat}", align="L")

        # Detail text
        pdf.set_xy(pdf.MARGIN + 5, fy + 11)
        pdf.set_font("Helvetica", "", 6.5)
        pdf.set_text_color(*C_TEXT2)
        pdf.cell(pdf.INNER_W - 10, 4, detail[:100], align="L")

        pdf.ln(18)


def _page_sentiment(pdf: InvestorReportPDF, report) -> None:
    """Page 3: Sentiment Analysis."""
    pdf.add_page()
    pdf._section_header("03  |  Sentiment Analysis", C_CONV)

    sent = report.sentiment
    overall_score  = float(getattr(sent, "overall_score",  0.0))
    bullish_count  = int(getattr(sent, "bullish_count",    0))
    bearish_count  = int(getattr(sent, "bearish_count",    0))
    neutral_count  = int(getattr(sent, "neutral_count",    0))
    top_keywords   = list(getattr(sent, "top_keywords",   []))
    overall_label  = _safe(getattr(sent, "overall_label",  "NEUTRAL"))
    news_score     = float(getattr(sent, "news_score",     0.0))
    freight_score  = float(getattr(sent, "freight_score",  0.0))
    macro_score    = float(getattr(sent, "macro_score",    0.0))
    alpha_score    = float(getattr(sent, "alpha_score",    0.0))

    # Sentiment breakdown bars
    total = bullish_count + bearish_count + neutral_count or 1
    bar_data = [
        ("Bullish",  bullish_count,  C_HIGH),
        ("Neutral",  neutral_count,  C_TEXT3),
        ("Bearish",  bearish_count,  C_LOW),
    ]
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*C_TEXT)
    pdf.cell(0, 5, "NEWS SENTIMENT BREAKDOWN", align="L")
    pdf.ln(6)

    label_w  = 22
    bar_w    = 100
    count_w  = 20
    score_w  = 25

    for label, count, color in bar_data:
        y = pdf.get_y()
        # Label
        pdf.set_xy(pdf.MARGIN, y)
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_text_color(*color)
        pdf.cell(label_w, 5, label, align="L")
        # Bar
        pdf._horizontal_bar(pdf.MARGIN + label_w, y + 1, bar_w, 3.5,
                            count, total, color)
        # Count
        pdf.set_xy(pdf.MARGIN + label_w + bar_w + 3, y)
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_text_color(*C_TEXT)
        pdf.cell(count_w, 5, f"{count} articles", align="L")
        # Percent
        pdf.set_xy(pdf.MARGIN + label_w + bar_w + count_w + 5, y)
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(*C_TEXT3)
        pdf.cell(score_w, 5, f"({count / total * 100:.1f}%)", align="L")
        pdf.ln(6)

    pdf._gap(4)

    # 4 component scores in small boxes
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*C_TEXT)
    pdf.cell(0, 5, "SENTIMENT COMPONENTS", align="L")
    pdf.ln(6)

    comp_w = (pdf.INNER_W - 9) / 4
    comp_h = 18
    comp_y = pdf.get_y()
    components = [
        ("News Score",     news_score,    C_CONV),
        ("Freight Score",  freight_score, C_ACCENT),
        ("Macro Score",    macro_score,   C_MOD),
        ("Alpha Score",    alpha_score,   C_HIGH),
    ]
    for i, (lbl, val, col) in enumerate(components):
        cx = pdf.MARGIN + i * (comp_w + 3)
        pdf._kpi_box(cx, comp_y, comp_w, comp_h, lbl,
                     f"{val:+.3f}", "Component", col)

    pdf.set_y(comp_y + comp_h + 8)

    # Top keywords as comma-separated styled list
    if top_keywords:
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_text_color(*C_TEXT)
        pdf.cell(0, 5, "TRENDING KEYWORDS & ENTITIES", align="L")
        pdf.ln(6)

        kw_str = ",  ".join(str(k) for k in top_keywords[:12])
        pdf.set_x(pdf.MARGIN)
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(*C_CONV)
        pdf.multi_cell(pdf.INNER_W, 5.5, kw_str, align="L")
        pdf.ln(4)

    # Sentiment narrative
    sentiment_narrative = getattr(report.ai, "sentiment_narrative", "")
    if sentiment_narrative:
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_text_color(*C_TEXT)
        pdf.cell(0, 5, "SENTIMENT NARRATIVE", align="L")
        pdf.ln(6)
        for para in _split_paragraphs(sentiment_narrative)[:2]:
            pdf._prose(para, size=8, color=C_TEXT2)
            pdf._gap(2)

    pdf._gap(4)

    # Top 5 news headlines in a table
    news_items = list(getattr(report, "news_items", []))[:5]
    if news_items:
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_text_color(*C_TEXT)
        pdf.cell(0, 5, "TOP NEWS HEADLINES", align="L")
        pdf.ln(6)

        rows = []
        for art in news_items:
            headline = _safe(getattr(art, "title", getattr(art, "headline", "—")))[:55]
            source   = _safe(getattr(art, "source", "—"))[:18]
            art_sent = _safe(getattr(art, "sentiment_label",
                              getattr(art, "sentiment", "—")))
            rows.append([headline, source, art_sent])

        pdf._row_table(
            headers=["Headline", "Source", "Sentiment"],
            rows=rows,
            col_widths=[110, 45, 32.9],
        )


def _page_alpha(pdf: InvestorReportPDF, report) -> None:
    """Page 4: Alpha Signal Intelligence."""
    pdf.add_page()
    pdf._section_header("04  |  Alpha Signal Intelligence", C_HIGH)

    alpha     = report.alpha
    portfolio = getattr(alpha, "portfolio", {}) or {}
    signals   = list(getattr(alpha, "signals", []))
    top_long  = list(getattr(alpha, "top_long",  []))
    top_short = list(getattr(alpha, "top_short", []))

    # Portfolio metrics box
    exp_ret  = float(portfolio.get("expected_return",  0.0))
    sharpe   = float(portfolio.get("sharpe",            0.0))
    vol      = float(portfolio.get("portfolio_vol",     0.0))
    max_dd   = float(portfolio.get("max_dd_estimate",   0.0))

    mbox_w = (pdf.INNER_W - 9) / 4
    mbox_h = 20
    mbox_y = pdf.get_y()
    metrics = [
        ("Expected Return",  f"{exp_ret:+.1f}%",   "Portfolio avg",     C_HIGH if exp_ret >= 0 else C_LOW),
        ("Sharpe Ratio",     f"{sharpe:.2f}",       "Risk-adjusted",     C_ACCENT),
        ("Annual Vol",       f"{vol:.1f}%",         "Estimated",         C_MOD),
        ("Max Drawdown Est", f"{max_dd:+.1f}%",    "Downside scenario", C_LOW),
    ]
    for i, (lbl, val, sub, col) in enumerate(metrics):
        mx = pdf.MARGIN + i * (mbox_w + 3)
        pdf._kpi_box(mx, mbox_y, mbox_w, mbox_h, lbl, val, sub, col)

    pdf.set_y(mbox_y + mbox_h + 8)

    # LONG signals table
    if top_long:
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*C_HIGH)
        pdf.cell(0, 5, "▲  LONG SIGNALS", align="L")
        pdf.ln(6)

        long_rows = []
        for sig in top_long[:6]:
            signal_name = _safe(getattr(sig, "signal_name", ""))[:28]
            ticker      = _safe(getattr(sig, "ticker",       "—"))
            conviction  = _safe(getattr(sig, "conviction",   "—"))
            entry       = _fmt_price(getattr(sig, "entry_price",       0.0))
            target      = _fmt_price(getattr(sig, "target_price",      0.0))
            ret_pct     = f"{getattr(sig, 'expected_return_pct', 0.0):+.1f}%"
            long_rows.append([signal_name, ticker, conviction, entry, target, ret_pct])

        pdf._row_table(
            headers=["Signal", "Ticker", "Conviction", "Entry", "Target", "Return"],
            rows=long_rows,
            col_widths=[60, 20, 24, 26, 26, 31.9],
        )
        pdf._gap(4)

    # SHORT signals table
    if top_short:
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*C_LOW)
        pdf.cell(0, 5, "▼  SHORT SIGNALS", align="L")
        pdf.ln(6)

        short_rows = []
        for sig in top_short[:4]:
            signal_name = _safe(getattr(sig, "signal_name", ""))[:28]
            ticker      = _safe(getattr(sig, "ticker",       "—"))
            conviction  = _safe(getattr(sig, "conviction",   "—"))
            entry       = _fmt_price(getattr(sig, "entry_price",       0.0))
            target      = _fmt_price(getattr(sig, "target_price",      0.0))
            ret_pct     = f"{getattr(sig, 'expected_return_pct', 0.0):+.1f}%"
            short_rows.append([signal_name, ticker, conviction, entry, target, ret_pct])

        pdf._row_table(
            headers=["Signal", "Ticker", "Conviction", "Entry", "Target", "Return"],
            rows=short_rows,
            col_widths=[60, 20, 24, 26, 26, 31.9],
        )
        pdf._gap(4)

    # No signals fallback
    if not top_long and not top_short:
        pdf._prose(
            "No alpha signals were generated in this cycle. "
            "This may indicate insufficient price momentum, conflicting macro signals, "
            "or incomplete data feeds. Monitor the next cycle for breakout conditions.",
            size=8, color=C_TEXT3,
        )
        pdf._gap(4)

    # Opportunity narrative
    opp_narrative = getattr(report.ai, "opportunity_narrative", "")
    if opp_narrative:
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_text_color(*C_TEXT)
        pdf.cell(0, 5, "OPPORTUNITY NARRATIVE", align="L")
        pdf.ln(6)
        for para in _split_paragraphs(opp_narrative)[:3]:
            pdf._prose(para, size=8, color=C_TEXT2)
            pdf._gap(2)


def _page_market_intelligence(pdf: InvestorReportPDF, report) -> None:
    """Page 5: Market Intelligence."""
    pdf.add_page()
    pdf._section_header("05  |  Market Intelligence", C_MOD)

    market = report.market

    # Risk level badge
    risk_level = _safe(getattr(market, "risk_level", "MODERATE"))
    risk_color = _RISK_COLORS.get(risk_level, C_MOD)

    ry = pdf.get_y()
    pdf.set_fill_color(*risk_color)
    pdf.rect(pdf.MARGIN, ry, 55, 10, "F")
    pdf.set_xy(pdf.MARGIN + 2, ry + 2)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*C_WHITE)
    pdf.cell(51, 6, f"RISK LEVEL: {risk_level}", align="C")
    pdf.set_xy(pdf.MARGIN + 60, ry + 2)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*C_TEXT3)
    active_opps = _fmt_int(getattr(market, "active_opportunities",  0))
    high_conv   = _fmt_int(getattr(market, "high_conviction_count", 0))
    pdf.cell(0, 6, f"Active Opportunities: {active_opps}   |   High Conviction: {high_conv}", align="L")
    pdf.ln(14)

    # Top 5 insights table
    top_insights = list(getattr(market, "top_insights", []))[:5]
    if top_insights:
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_text_color(*C_TEXT)
        pdf.cell(0, 5, "TOP MARKET INSIGHTS", align="L")
        pdf.ln(6)

        rows = []
        for ins in top_insights:
            title  = _safe(getattr(ins, "title",    ""))[:38]
            action = _safe(getattr(ins, "action",   "—"))
            score  = float(getattr(ins, "score",    0.0))
            cat    = _safe(getattr(ins, "category", "—"))
            rows.append([title, action, f"{score:.2f}", cat])

        pdf._row_table(
            headers=["Insight Title", "Action", "Score", "Category"],
            rows=rows,
            col_widths=[88, 26, 22, 51.9],
        )
        pdf._gap(6)

    # Top ports table
    top_ports = list(getattr(market, "top_ports", []))[:5]
    if top_ports:
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_text_color(*C_TEXT)
        pdf.cell(0, 5, "TOP PORTS BY DEMAND", align="L")
        pdf.ln(6)

        port_rows = []
        for pr in top_ports:
            if isinstance(pr, dict):
                name   = _safe(pr.get("port_name") or pr.get("port") or pr.get("name") or pr.get("locode"))[:28]
                region = _safe(pr.get("region",  "—"))[:20]
                score  = float(pr.get("demand_score") or pr.get("score", 0.0) or 0.0)
                trend  = _safe(pr.get("trend",   "—"))[:12]
                status = _safe(pr.get("status") or pr.get("demand_label", "—"))[:12]
            else:
                name   = _safe(getattr(pr, "port_name", getattr(pr, "port_id", "—")))[:28]
                region = _safe(getattr(pr, "region",  "—"))[:20]
                score  = float(getattr(pr, "demand_score", 0.0))
                trend  = _safe(getattr(pr, "trend",  "—"))[:12]
                status = _safe(getattr(pr, "demand_label", "—"))[:12]
            port_rows.append([name, region, f"{score:.2f}", trend, status])

        pdf._row_table(
            headers=["Port", "Region", "Score", "Trend", "Status"],
            rows=port_rows,
            col_widths=[60, 45, 20, 30, 32.9],
        )
        pdf._gap(6)

    # Top routes table
    top_routes = list(getattr(market, "top_routes", []))[:5]
    if top_routes:
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_text_color(*C_TEXT)
        pdf.cell(0, 5, "TOP ROUTES", align="L")
        pdf.ln(6)

        route_rows = []
        for rr in top_routes:
            if isinstance(rr, dict):
                name  = _safe(rr.get("route") or rr.get("lane") or rr.get("name", "—"))[:38]
                rate  = f"${rr.get('rate', rr.get('current_rate', 0.0)):,.0f}"
                chg   = f"{rr.get('change_pct', 0.0):+.1f}%"
                trend = _safe(rr.get("label", rr.get("trend", "—")))[:12]
            else:
                name  = _safe(getattr(rr, "route", getattr(rr, "name", "—")))[:38]
                rate  = f"${getattr(rr, 'rate', 0.0):,.0f}"
                chg   = f"{getattr(rr, 'change_pct', 0.0):+.1f}%"
                trend = _safe(getattr(rr, "label", "—"))[:12]
            route_rows.append([name, rate, chg, trend])

        pdf._row_table(
            headers=["Route", "Rate ($/FEU)", "30d Change", "Trend"],
            rows=route_rows,
            col_widths=[90, 35, 30, 32.9],
        )


def _page_freight_macro(pdf: InvestorReportPDF, report) -> None:
    """Page 6: Freight Rates & Macro."""
    pdf.add_page()
    pdf._section_header("06  |  Freight Rates & Macro Environment", C_ACCENT)

    freight = report.freight
    macro   = report.macro

    # Freight rate momentum summary boxes
    avg_chg   = float(getattr(freight, "avg_change_30d_pct", 0.0))
    momentum  = _safe(getattr(freight, "momentum_label",     "Stable"))
    fbx_comp  = float(getattr(freight, "fbx_composite",      0.0))
    biggest   = getattr(freight, "biggest_mover",            {}) or {}
    biggest_id  = _safe(biggest.get("route_id", "—"))[:20]
    biggest_chg = float(biggest.get("change_pct", 0.0))

    fbox_w = (pdf.INNER_W - 9) / 4
    fbox_h = 20
    fbox_y = pdf.get_y()
    fboxes = [
        ("Avg 30d Change",   f"{avg_chg:+.1f}%",  "All routes",       C_HIGH if avg_chg >= 0 else C_LOW),
        ("Momentum",          momentum,             "Rate direction",   C_ACCENT),
        ("FBX Composite",    f"${fbx_comp:,.0f}", "/FEU avg",          C_CONV),
        ("Biggest Mover",    f"{biggest_chg:+.1f}%", biggest_id[:15], C_MOD),
    ]
    for i, (lbl, val, sub, col) in enumerate(fboxes):
        fx = pdf.MARGIN + i * (fbox_w + 3)
        pdf._kpi_box(fx, fbox_y, fbox_w, fbox_h, lbl, val, sub, col)

    pdf.set_y(fbox_y + fbox_h + 8)

    # Routes table
    freight_routes = list(getattr(freight, "routes", []))[:8]
    if freight_routes:
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_text_color(*C_TEXT)
        pdf.cell(0, 5, "FREIGHT RATE SNAPSHOT BY ROUTE", align="L")
        pdf.ln(6)

        frt_rows = []
        for rt in freight_routes:
            if isinstance(rt, dict):
                route_id  = _safe(rt.get("route_id", rt.get("route", "—")))[:35]
                rate      = f"${rt.get('rate', 0.0):,.0f}"
                chg_30d   = f"${rt.get('change_30d', 0.0):+,.0f}"
                chg_pct   = f"{rt.get('change_pct', 0.0):+.1f}%"
                trend_lbl = _safe(rt.get("label", rt.get("trend", "—")))[:12]
            else:
                route_id  = _safe(getattr(rt, "route_id", getattr(rt, "route", "—")))[:35]
                rate      = f"${getattr(rt, 'rate', 0.0):,.0f}"
                chg_30d   = f"${getattr(rt, 'change_30d', 0.0):+,.0f}"
                chg_pct   = f"{getattr(rt, 'change_pct', 0.0):+.1f}%"
                trend_lbl = _safe(getattr(rt, "label", "—"))[:12]
            frt_rows.append([route_id, rate, chg_30d, chg_pct, trend_lbl])

        pdf._row_table(
            headers=["Route", "Rate ($/FEU)", "30d Change ($)", "30d Change (%)", "Trend"],
            rows=frt_rows,
            col_widths=[72, 30, 32, 32, 21.9],
        )
        pdf._gap(6)

    # Macro snapshot boxes
    pdf.set_font("Helvetica", "B", 7.5)
    pdf.set_text_color(*C_TEXT)
    pdf.cell(0, 5, "MACRO SNAPSHOT", align="L")
    pdf.ln(6)

    bdi      = float(getattr(macro, "bdi",               0.0))
    bdi_chg  = float(getattr(macro, "bdi_change_30d_pct", 0.0))
    wti      = float(getattr(macro, "wti",               0.0))
    wti_chg  = float(getattr(macro, "wti_change_30d_pct", 0.0))
    tsy      = float(getattr(macro, "treasury_10y",      0.0))
    stress   = _safe(getattr(macro, "supply_chain_stress", "MODERATE"))
    stress_col = _RISK_COLORS.get(stress, C_MOD)

    mbox_w = (pdf.INNER_W - 9) / 4
    mbox_h = 20
    mbox_y = pdf.get_y()
    mboxes = [
        ("Baltic Dry Index",   f"{int(bdi)}" if bdi else "N/A",  f"{bdi_chg:+.1f}% 30d", C_ACCENT if bdi_chg >= 0 else C_LOW),
        ("WTI Crude Oil",      f"${wti:.2f}" if wti else "N/A",  f"{wti_chg:+.1f}% 30d",  C_MOD),
        ("10Y Treasury",       f"{tsy:.2f}%" if tsy else "N/A",  "US Yield",               C_CONV),
        ("Supply Chain Stress", stress,                           "BDI+WTI+Rates",          stress_col),
    ]
    for i, (lbl, val, sub, col) in enumerate(mboxes):
        mx = pdf.MARGIN + i * (mbox_w + 3)
        pdf._kpi_box(mx, mbox_y, mbox_w, mbox_h, lbl, val, sub, col)

    pdf.set_y(mbox_y + mbox_h + 8)

    # Risk narrative
    risk_narrative = getattr(report.ai, "risk_narrative", "")
    if risk_narrative:
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_text_color(*C_TEXT)
        pdf.cell(0, 5, "RISK NARRATIVE", align="L")
        pdf.ln(6)
        for para in _split_paragraphs(risk_narrative)[:3]:
            pdf._prose(para, size=8, color=C_TEXT2)
            pdf._gap(2)


def _page_stocks(pdf: InvestorReportPDF, report) -> None:
    """Page 7: Stocks & Recommendations."""
    pdf.add_page()
    pdf._section_header("07  |  Shipping Equities & Recommendations", C_HIGH)

    stocks_obj   = report.stocks
    tickers      = list(getattr(stocks_obj, "tickers",      []))
    prices       = dict(getattr(stocks_obj, "prices",       {}))
    changes_30d  = dict(getattr(stocks_obj, "changes_30d",  {}))
    signals_by_t = dict(getattr(stocks_obj, "signals_by_ticker", {}))
    top_pick     = _safe(getattr(stocks_obj, "top_pick",       "—"))
    top_rationale= _safe(getattr(stocks_obj, "top_pick_rationale", ""))

    # Stock summary table
    if tickers:
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_text_color(*C_TEXT)
        pdf.cell(0, 5, "SHIPPING EQUITY SUMMARY", align="L")
        pdf.ln(6)

        stock_rows = []
        for t in tickers:
            price   = prices.get(t, 0.0)
            chg30   = changes_30d.get(t, 0.0)
            t_sigs  = signals_by_t.get(t, [])
            n_sigs  = len(t_sigs)
            top_sig = _safe(getattr(t_sigs[0], "signal_name", "—")) if t_sigs else "—"
            chg_str = f"{chg30:+.1f}%"
            stock_rows.append([t, _fmt_price(price), chg_str, _fmt_int(n_sigs), top_sig[:30]])

        pdf._row_table(
            headers=["Ticker", "Price", "30d Change", "Signals", "Top Signal"],
            rows=stock_rows,
            col_widths=[22, 26, 26, 20, 93.9],
        )
        pdf._gap(6)

    # Top pick highlighted box
    if top_pick and top_pick != "—":
        pick_price  = prices.get(top_pick, 0.0)
        pick_chg    = changes_30d.get(top_pick, 0.0)
        pick_col    = C_HIGH if pick_chg >= 0 else C_LOW

        hy = pdf.get_y()
        if pdf.will_page_break(24):
            pdf.add_page()
            hy = pdf.get_y()

        pdf.set_fill_color(*C_CARD)
        pdf.rect(pdf.MARGIN, hy, pdf.INNER_W, 24, "F")
        pdf.set_fill_color(*C_HIGH)
        pdf.rect(pdf.MARGIN, hy, 2.5, 24, "F")
        pdf.set_draw_color(*C_HIGH)
        pdf.set_line_width(0.4)
        pdf.rect(pdf.MARGIN, hy, pdf.INNER_W, 24)

        pdf.set_xy(pdf.MARGIN + 6, hy + 2)
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_text_color(*C_HIGH)
        pdf.cell(40, 4, "TOP PICK", align="L")

        pdf.set_xy(pdf.MARGIN + 6, hy + 7)
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(*C_TEXT)
        pdf.cell(30, 8, top_pick, align="L")

        pdf.set_xy(pdf.MARGIN + 38, hy + 9)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*pick_col)
        pdf.cell(25, 6, f"{pick_chg:+.1f}%", align="L")

        pdf.set_xy(pdf.MARGIN + 70, hy + 7)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*C_TEXT2)
        pdf.cell(25, 6, _fmt_price(pick_price), align="L")

        pdf.set_xy(pdf.MARGIN + 6, hy + 16)
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(*C_TEXT2)
        pdf.cell(pdf.INNER_W - 12, 4, top_rationale[:130], align="L")

        pdf.set_y(hy + 28)

    # Recommendations section
    top_recommendations = list(getattr(report.ai, "top_recommendations", []))
    if top_recommendations:
        pdf._gap(4)
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_text_color(*C_TEXT)
        pdf.cell(0, 5, "STRUCTURED RECOMMENDATIONS", align="L")
        pdf.ln(6)

        for rec in top_recommendations[:5]:
            rank      = rec.get("rank",            "")
            title     = _safe(rec.get("title",     "Recommendation"))[:55]
            action    = _safe(rec.get("action",    "MONITOR"))
            ticker    = _safe(rec.get("ticker",    "—"))
            conviction= _safe(rec.get("conviction","MEDIUM"))
            exp_ret   = float(rec.get("expected_return", 0.0))
            horizon   = _safe(rec.get("time_horizon",  "1M"))
            rationale = _safe(rec.get("rationale",      ""))[:160]
            entry     = rec.get("entry",  0.0)
            target    = rec.get("target", 0.0)

            action_color = {"BUY": C_HIGH, "SELL": C_LOW, "SHORT": C_LOW,
                            "HOLD": C_MOD, "MONITOR": C_ACCENT}.get(action, C_ACCENT)
            conv_color   = _CONVICTION_COLORS.get(conviction, C_MOD)

            cy = pdf.get_y()
            card_h = 24 if rationale else 18
            if pdf.will_page_break(card_h + 4):
                pdf.add_page()
                cy = pdf.get_y()

            pdf.set_fill_color(*C_CARD)
            pdf.rect(pdf.MARGIN, cy, pdf.INNER_W, card_h, "F")
            pdf.set_fill_color(*action_color)
            pdf.rect(pdf.MARGIN, cy, 2.5, card_h, "F")

            # Rank + title
            pdf.set_xy(pdf.MARGIN + 6, cy + 2)
            pdf.set_font("Helvetica", "B", 7.5)
            pdf.set_text_color(*C_TEXT)
            pdf.cell(pdf.INNER_W - 60, 5, f"#{rank}  {title}", align="L")

            # Action badge
            pdf.set_fill_color(*action_color)
            pdf.rect(pdf.MARGIN + pdf.INNER_W - 48, cy + 2, 22, 5, "F")
            pdf.set_xy(pdf.MARGIN + pdf.INNER_W - 48, cy + 2.5)
            pdf.set_font("Helvetica", "B", 6.5)
            pdf.set_text_color(*C_WHITE)
            pdf.cell(22, 4, action, align="C")

            # Conviction badge
            pdf.set_fill_color(*conv_color)
            pdf.rect(pdf.MARGIN + pdf.INNER_W - 23, cy + 2, 20, 5, "F")
            pdf.set_xy(pdf.MARGIN + pdf.INNER_W - 23, cy + 2.5)
            pdf.set_font("Helvetica", "B", 6.5)
            pdf.set_text_color(*C_WHITE)
            pdf.cell(18, 4, conviction, align="C")

            # Ticker / horizon / return
            pdf.set_xy(pdf.MARGIN + 6, cy + 9)
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(*C_TEXT3)
            price_str = f"  |  Entry: {_fmt_price(entry)}  →  Target: {_fmt_price(target)}" if entry else ""
            pdf.cell(pdf.INNER_W - 12, 4,
                     f"Ticker: {ticker}  |  Horizon: {horizon}  |  Exp. Return: {exp_ret:+.1f}%{price_str}",
                     align="L")

            # Rationale
            if rationale:
                pdf.set_xy(pdf.MARGIN + 6, cy + 15)
                pdf.set_font("Helvetica", "I", 6.5)
                pdf.set_text_color(*C_TEXT2)
                pdf.cell(pdf.INNER_W - 12, 4, rationale[:160], align="L")

            pdf.ln(card_h + 5)

    # 30-day outlook
    outlook = getattr(report.ai, "outlook_30d", "")
    if outlook:
        if pdf.will_page_break(20):
            pdf.add_page()
        pdf._gap(4)
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_text_color(*C_TEXT)
        pdf.cell(0, 5, "30-DAY OUTLOOK", align="L")
        pdf.ln(6)
        pdf._prose(outlook, size=8, color=C_TEXT2)


def _page_disclaimer(pdf: InvestorReportPDF, report) -> None:
    """Page 8: Disclaimer & Data Sources."""
    pdf.add_page()
    pdf._section_header("08  |  Disclaimer & Data Sources", C_TEXT3)

    disclaimer = getattr(report.ai, "disclaimer", "")
    if not disclaimer:
        disclaimer = (
            "IMPORTANT DISCLAIMER: This report is generated for informational and "
            "research purposes only. It does not constitute investment advice, a "
            "solicitation to buy or sell any security, or a recommendation of any "
            "specific investment strategy. Past performance is not indicative of future "
            "results. All investments involve risk, including the possible loss of principal. "
            "Shipping equities are highly volatile and subject to sector-specific risks. "
            "Always consult a qualified financial professional before making investment decisions."
        )

    pdf.set_font("Helvetica", "B", 7.5)
    pdf.set_text_color(*C_TEXT)
    pdf.cell(0, 5, "INVESTMENT DISCLAIMER", align="L")
    pdf.ln(7)

    # Split disclaimer into paragraphs and render
    paras = _split_paragraphs(disclaimer)
    for para in paras:
        pdf.set_x(pdf.MARGIN)
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(*C_TEXT2)
        pdf.multi_cell(pdf.INNER_W, 4.5, para, align="J")
        pdf.ln(3)

    pdf._gap(8)

    # Data sources list
    pdf.set_font("Helvetica", "B", 7.5)
    pdf.set_text_color(*C_TEXT)
    pdf.cell(0, 5, "DATA SOURCES", align="L")
    pdf.ln(7)

    sources = [
        ("Freight Rates",     "Freightos Baltic Index (FBX), Baltic Exchange"),
        ("Macroeconomic",     "US Federal Reserve Economic Data (FRED API)"),
        ("Trade Flows",       "UN Comtrade, World Bank WITS"),
        ("Shipping Equities", "Yahoo Finance (yfinance)"),
        ("Port AIS Data",     "MarineTraffic / AIS aggregation"),
        ("Port Registry",     "World Port Index (UKHO)"),
        ("News & Sentiment",  "RSS feeds, financial newswires, NLP sentiment engine"),
    ]

    for src, desc in sources:
        sy = pdf.get_y()
        pdf.set_fill_color(*C_CARD)
        pdf.rect(pdf.MARGIN, sy, pdf.INNER_W, 7, "F")
        pdf.set_fill_color(*C_ACCENT)
        pdf.rect(pdf.MARGIN, sy, 1.5, 7, "F")
        pdf.set_xy(pdf.MARGIN + 4, sy + 1.5)
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_text_color(*C_TEXT)
        pdf.cell(45, 4, src, align="L")
        pdf.set_xy(pdf.MARGIN + 50, sy + 1.5)
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(*C_TEXT2)
        pdf.cell(pdf.INNER_W - 54, 4, desc, align="L")
        pdf.ln(8)

    pdf._gap(8)

    # Generated timestamp
    pdf.set_font("Helvetica", "", 7)
    pdf.set_text_color(*C_TEXT3)
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    pdf.cell(0, 5, f"Generated: {now_str}  |  Ship Tracker Intelligence Platform", align="C")
    pdf.ln(6)
    pdf.set_x(pdf.MARGIN)
    pdf.set_font("Helvetica", "I", 6.5)
    pdf.cell(0, 4, "Report ID: " + _safe(report.generated_at), align="C")


# ---------------------------------------------------------------------------
# Public export function
# ---------------------------------------------------------------------------

def render_investor_report_pdf(report: "InvestorReport") -> bytes:
    """Generate a complete investor report PDF and return it as bytes.

    Suitable for use with st.download_button:
        st.download_button("Download PDF", pdf_bytes, "report.pdf", "application/pdf")

    Parameters
    ----------
    report : InvestorReport
        A fully populated InvestorReport object from
        processing.investor_report_engine.build_investor_report().

    Returns
    -------
    bytes
        Raw PDF bytes.

    Raises
    ------
    ImportError
        If fpdf2 is not installed.
    """
    if not _FPDF_OK:
        raise ImportError(
            "fpdf2 is required to generate PDF reports. "
            "Install it with: pip install fpdf2"
        )

    try:
        pdf = InvestorReportPDF()

        # Page 1: Cover
        try:
            _page_cover(pdf, report)
        except Exception as exc:
            pdf.add_page()
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(239, 68, 68)
            pdf.cell(0, 10, f"Cover page error: {exc}", align="L")

        # Page 2: Executive Summary
        try:
            _page_executive_summary(pdf, report)
        except Exception as exc:
            pdf.add_page()
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(0, 10, f"Executive Summary error: {exc}", align="L")

        # Page 3: Sentiment Analysis
        try:
            _page_sentiment(pdf, report)
        except Exception as exc:
            pdf.add_page()
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(0, 10, f"Sentiment page error: {exc}", align="L")

        # Page 4: Alpha Signal Intelligence
        try:
            _page_alpha(pdf, report)
        except Exception as exc:
            pdf.add_page()
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(0, 10, f"Alpha page error: {exc}", align="L")

        # Page 5: Market Intelligence
        try:
            _page_market_intelligence(pdf, report)
        except Exception as exc:
            pdf.add_page()
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(0, 10, f"Market Intelligence page error: {exc}", align="L")

        # Page 6: Freight Rates & Macro
        try:
            _page_freight_macro(pdf, report)
        except Exception as exc:
            pdf.add_page()
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(0, 10, f"Freight & Macro page error: {exc}", align="L")

        # Page 7: Stocks & Recommendations
        try:
            _page_stocks(pdf, report)
        except Exception as exc:
            pdf.add_page()
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(0, 10, f"Stocks page error: {exc}", align="L")

        # Page 8: Disclaimer
        try:
            _page_disclaimer(pdf, report)
        except Exception as exc:
            pdf.add_page()
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(0, 10, f"Disclaimer page error: {exc}", align="L")

        # Output to bytes
        return bytes(pdf.output())

    except Exception as exc:
        # Last-resort fallback: return a minimal error PDF
        try:
            err_pdf = FPDF(orientation="P", unit="mm", format="Letter")
            err_pdf.add_page()
            err_pdf.set_font("Helvetica", "B", 12)
            err_pdf.set_text_color(239, 68, 68)
            err_pdf.cell(0, 10, "PDF Generation Failed", align="L")
            err_pdf.ln(8)
            err_pdf.set_font("Helvetica", "", 9)
            err_pdf.set_text_color(100, 116, 139)
            err_pdf.multi_cell(0, 6,
                f"An error occurred during PDF generation:\n\n{exc}\n\n"
                f"{traceback.format_exc()[:600]}",
                align="L",
            )
            return bytes(err_pdf.output())
        except Exception:
            # Absolute last resort: return empty bytes
            return b""
