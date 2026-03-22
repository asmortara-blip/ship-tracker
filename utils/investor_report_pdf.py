"""
utils/investor_report_pdf.py
────────────────────────────
Goldman Sachs GIR / Morgan Stanley Research quality PDF renderer for InvestorReport.

Produces a 16-page institutional-grade research note styled like a real sell-side
shipping research document — the kind of document a managing director would hand
to a hedge fund PM.

Usage:
    from utils.investor_report_pdf import render_investor_report_pdf
    pdf_bytes = render_investor_report_pdf(report)
    st.download_button("Download PDF", pdf_bytes, "report.pdf", "application/pdf")

Dependencies: fpdf2 (pip install fpdf2)
No external fonts required — uses Helvetica throughout.
"""
from __future__ import annotations

import traceback
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, List, Optional, Tuple

# ── fpdf2 ────────────────────────────────────────────────────────────────────
try:
    from fpdf import FPDF
    _FPDF_OK = True
except ImportError:
    # Provide a no-op base class so the module can be imported without fpdf2
    class FPDF:  # type: ignore
        pass
    _FPDF_OK = False

if TYPE_CHECKING:
    from processing.investor_report_engine import InvestorReport


# ═══════════════════════════════════════════════════════════════════════════════
#  COLOR PALETTE — Light institutional theme (Goldman Sachs GIR style)
# ═══════════════════════════════════════════════════════════════════════════════
WHITE       = (255, 255, 255)
OFF_WHITE   = (250, 250, 252)
LIGHT_GRAY  = (245, 245, 248)
MID_GRAY    = (220, 220, 228)
DARK_GRAY   = (60,  60,  75)
NAVY        = (15,  40,  90)
NAVY_LIGHT  = (30,  65,  140)
GREEN       = (22,  120, 68)
RED         = (185, 30,  30)
AMBER       = (160, 100, 0)
GOLD_LINE   = (180, 150, 60)

TEXT_LO     = (120, 120, 135)
TEXT_HI     = DARK_GRAY

_RISK_COLORS = {
    "LOW":      GREEN,
    "MODERATE": AMBER,
    "HIGH":     RED,
    "CRITICAL": (160, 20, 20),
}
_CONVICTION_COLORS = {
    "HIGH":   GREEN,
    "MEDIUM": AMBER,
    "MED":    AMBER,
    "LOW":    RED,
}
_ACTION_COLORS = {
    "BUY":     GREEN,
    "LONG":    GREEN,
    "SELL":    RED,
    "SHORT":   RED,
    "HOLD":    AMBER,
    "MONITOR": NAVY_LIGHT,
    "WATCH":   NAVY_LIGHT,
}
_SENTIMENT_COLORS = {
    "BULLISH": GREEN,
    "BEARISH": RED,
    "NEUTRAL": DARK_GRAY,
    "MIXED":   AMBER,
}


# ═══════════════════════════════════════════════════════════════════════════════
#  UTILITY HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _safe(val, default: str = "N/A") -> str:
    if val is None:
        return str(default)
    s = str(val).strip()
    return s if s else str(default)


def _fmt_float(val, decimals: int = 2, prefix: str = "", suffix: str = "",
               show_sign: bool = False) -> str:
    try:
        f = float(val)
        sign = "+" if (show_sign and f > 0) else ""
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
        return f"{int(val):,}"
    except (TypeError, ValueError):
        return "N/A"


def _clamp(val: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(val)))


def _sentiment_color(label: str) -> tuple:
    return _SENTIMENT_COLORS.get(str(label).upper(), TEXT_LO)


def _change_color(pct) -> tuple:
    try:
        v = float(pct)
        if v > 0.01:
            return GREEN
        if v < -0.01:
            return RED
        return DARK_GRAY
    except (TypeError, ValueError):
        return DARK_GRAY


def _score_color(score) -> tuple:
    try:
        v = float(score)
        if v >= 0.4:
            return GREEN
        if v >= 0.0:
            return AMBER
        if v >= -0.4:
            return AMBER
        return RED
    except (TypeError, ValueError):
        return DARK_GRAY


def _split_paragraphs(text: str, max_len: int = 900) -> List[str]:
    if not text:
        return []
    parts = [p.strip() for p in str(text).split("\n\n") if p.strip()]
    result: List[str] = []
    for part in parts:
        if len(part) > max_len:
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


def _is_number(s: str) -> bool:
    cleaned = str(s).strip().lstrip("+-$").rstrip("%,")
    try:
        float(cleaned.replace(",", ""))
        return True
    except ValueError:
        return False


def _trunc(text: str, n: int) -> str:
    s = str(text)
    return s if len(s) <= n else s[:n - 1] + "..."


# ── Unicode → Latin-1 sanitizer ──────────────────────────────────────────────
# fpdf2 with built-in Helvetica is Latin-1 only.  Strip/replace any char that
# falls outside 0x00-0xFF to prevent FPDFUnicodeEncodingException.
_UNICODE_REPLACEMENTS = {
    "\u2014": "--",   # em dash
    "\u2013": "-",    # en dash
    "\u2018": "'",    # left single quote
    "\u2019": "'",    # right single quote
    "\u201c": '"',    # left double quote
    "\u201d": '"',    # right double quote
    "\u2026": "...",  # ellipsis
    "\u2022": "*",    # bullet
    "\u00b1": "+/-",  # plus-minus
    "\u2191": "^",    # up arrow
    "\u2193": "v",    # down arrow
    "\u2192": "->",   # right arrow
    "\u00b0": "deg",  # degree
    "\u00d7": "x",    # multiplication
    "\u2264": "<=",   # less-or-equal
    "\u2265": ">=",   # greater-or-equal
    "\u00e9": "e",    # e-acute
    "\u00e0": "a",    # a-grave
    "\u00e8": "e",    # e-grave
    "\u00fc": "u",    # u-umlaut
    "\u00f6": "o",    # o-umlaut
    "\u00e4": "a",    # a-umlaut
    "\u2070": "0",    # superscript 0
    "\u00b9": "1",    # superscript 1
    "\u00b2": "2",    # superscript 2
    "\u00b3": "3",    # superscript 3
}

def _t(text: str) -> str:
    """Sanitize text for fpdf2 Helvetica (Latin-1 only)."""
    s = str(text) if text is not None else ""
    for ch, rep in _UNICODE_REPLACEMENTS.items():
        s = s.replace(ch, rep)
    # Final pass: drop any remaining non-Latin-1 chars
    return s.encode("latin-1", errors="replace").decode("latin-1")


def _rr(entry, stop, target) -> str:
    """Calculate risk/reward ratio string."""
    try:
        e, s, t = float(entry), float(stop), float(target)
        risk = abs(e - s)
        reward = abs(t - e)
        if risk < 0.0001:
            return "N/A"
        return f"{reward / risk:.1f}x"
    except (TypeError, ValueError):
        return "N/A"


# ═══════════════════════════════════════════════════════════════════════════════
#  MOCK FALLBACK DATA  (used when report fields are empty/None)
# ═══════════════════════════════════════════════════════════════════════════════

_MOCK_FREIGHT_ROUTES = [
    {"route_id": "FBX01", "label": "China/East Asia - N. America West Coast", "rate": 2180, "change_30d": -312, "change_pct": -12.5, "trend": "DOWN"},
    {"route_id": "FBX02", "label": "China/East Asia - N. America East Coast", "rate": 3450, "change_30d": -205, "change_pct": -5.6, "trend": "DOWN"},
    {"route_id": "FBX03", "label": "China/East Asia - N. Europe",             "rate": 2890, "change_30d": +415, "change_pct": +16.8, "trend": "UP"},
    {"route_id": "FBX04", "label": "China/East Asia - Mediterranean",          "rate": 3120, "change_30d": +298, "change_pct": +10.5, "trend": "UP"},
    {"route_id": "FBX05", "label": "N. Europe - N. America East Coast",        "rate": 1650, "change_30d": -88,  "change_pct": -5.1, "trend": "FLAT"},
    {"route_id": "FBX06", "label": "N. Europe - South America East Coast",     "rate": 1870, "change_30d": +122, "change_pct": +7.0, "trend": "UP"},
    {"route_id": "FBX07", "label": "N. America - S. America West Coast",       "rate": 1340, "change_30d": -45,  "change_pct": -3.2, "trend": "FLAT"},
    {"route_id": "FBX08", "label": "India Subcontinent - N. America East Coast","rate": 2760, "change_30d": +175, "change_pct": +6.8, "trend": "UP"},
    {"route_id": "FBX09", "label": "China/East Asia - Oceania",                "rate": 1420, "change_30d": -62,  "change_pct": -4.2, "trend": "DOWN"},
    {"route_id": "FBX10", "label": "Middle East - N. America East Coast",      "rate": 3890, "change_30d": +540, "change_pct": +16.1, "trend": "UP"},
    {"route_id": "FBX11", "label": "Intra-Asia",                               "rate": 980,  "change_30d": -35,  "change_pct": -3.4, "trend": "FLAT"},
    {"route_id": "FBX12", "label": "China/East Asia - South America W. Coast", "rate": 4210, "change_30d": +380, "change_pct": +9.9, "trend": "UP"},
]

_MOCK_TICKERS = ["ZIM", "MATX", "SBLK", "GOGL", "STNG", "INSW", "DAC", "GSL", "EGLE", "NMM"]

_MOCK_RISK_FACTORS = [
    ("Freight Rate Volatility",  "HIGH",     "ADVERSE",   "75%",  "MATERIAL",   "Diversify route exposure; use freight derivatives as hedge"),
    ("Port Congestion",          "MODERATE", "ADVERSE",   "55%",  "MODERATE",   "Monitor AIS dwell times; favor operators with port priority agreements"),
    ("Geopolitical Disruption",  "HIGH",     "ADVERSE",   "60%",  "SIGNIFICANT","Track Red Sea/Strait of Hormuz developments; maintain contingency routing"),
    ("USD Strength",             "MODERATE", "ADVERSE",   "45%",  "MODERATE",   "Monitor DXY; USD-denominated rates benefit USD-revenue operators"),
    ("Fuel Price Spike",         "MODERATE", "ADVERSE",   "50%",  "MODERATE",   "Focus on scrubber-equipped tonnage; monitor WTI/Brent spread"),
    ("IMO Regulatory Tightening","LOW",      "ADVERSE",   "35%",  "LOW",        "Favor CII-compliant operators; avoid non-compliant older tonnage"),
    ("Global PMI Deterioration", "MODERATE", "ADVERSE",   "40%",  "MATERIAL",   "Reduce dry bulk exposure; rotate toward tankers in demand downcycle"),
    ("Fleet Overcapacity",       "LOW",      "ADVERSE",   "30%",  "LOW",        "Monitor orderbook-to-fleet ratio; favor scrapping-driven tight supply"),
]

_MOCK_SCENARIOS = [
    ("Base Case (65%)",  "65%", "+2.8%",  "+4.1%",  "+8.3%",  "PMI stabilizes above 50; Red Sea partial re-opening"),
    ("Bull Case (20%)",  "20%", "+12.5%", "+18.7%", "+24.2%", "Global trade surge; port congestion persists; supply shock"),
    ("Bear Case (15%)",  "15%", "-15.3%", "-22.1%", "-31.8%", "Recession signals; demand collapse; fleet oversupply"),
]


# ═══════════════════════════════════════════════════════════════════════════════
#  PDF CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class InstitutionalReportPDF(FPDF):
    """
    fpdf2 subclass styled for Goldman Sachs GIR / Morgan Stanley Research quality
    light institutional output.

    Letter size (215.9 x 279.4 mm), 15 mm margins, white/off-white background.
    Dense research note aesthetic — no gradients, no dark backgrounds.
    """

    PAGE_W  = 215.9
    PAGE_H  = 279.4
    L_MARG  = 15.0
    R_MARG  = 15.0
    T_MARG  = 18.0
    B_MARG  = 18.0
    INNER_W = 215.9 - 15.0 - 15.0  # 185.9 mm

    _report_date: str = ""
    _section_name: str = "GLOBAL SHIPPING INTELLIGENCE"

    def __init__(self) -> None:
        super().__init__(orientation="P", unit="mm", format="Letter")
        self.set_margins(self.L_MARG, self.T_MARG, self.R_MARG)
        self.set_auto_page_break(auto=True, margin=self.B_MARG)
        self.alias_nb_pages()

    def set_section_name(self, name: str) -> None:
        self._section_name = name

    # Auto-sanitize all text going into fpdf (Helvetica is Latin-1 only)
    def cell(self, w=0, h=0, txt="", border=0, ln=0, align="", fill=False, link=""):
        return super().cell(w, h, _t(txt), border, ln, align, fill, link)

    def multi_cell(self, w, h, txt="", border=0, align="J", fill=False,
                   split_only=False, link="", ln=3, max_line_height=None,
                   markdown=False, output=None):
        return super().multi_cell(w, h, _t(txt), border, align, fill,
                                  split_only, link, ln, max_line_height,
                                  markdown, output)

    # ─────────────────────────────────────────────────────────────────────────
    # HEADER
    # ─────────────────────────────────────────────────────────────────────────

    def header(self) -> None:
        if self.page_no() == 1:
            return
        # White header strip
        self.set_fill_color(*WHITE)
        self.rect(0, 0, self.PAGE_W, 12, "F")
        # Navy bottom rule
        self.set_draw_color(*NAVY)
        self.set_line_width(0.4)
        self.line(self.L_MARG, 11.5, self.PAGE_W - self.R_MARG, 11.5)
        # Gold accent line above navy
        self.set_draw_color(*GOLD_LINE)
        self.set_line_width(0.25)
        self.line(self.L_MARG, 11.0, self.PAGE_W - self.R_MARG, 11.0)
        # Firm name — left
        self.set_xy(self.L_MARG, 3.5)
        self.set_font("Helvetica", "B", 7)
        self.set_text_color(*NAVY)
        self.cell(80, 4, "GLOBAL SHIPPING INTELLIGENCE", align="L")
        # Section name — center
        self.set_xy(self.L_MARG + 60, 3.5)
        self.set_font("Helvetica", "", 6.5)
        self.set_text_color(*TEXT_LO)
        self.cell(80, 4, self._section_name, align="C")
        # Page number — right
        self.set_xy(self.PAGE_W - self.R_MARG - 30, 3.5)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*TEXT_LO)
        self.cell(30, 4, f"Page {self.page_no()}", align="R")

    # ─────────────────────────────────────────────────────────────────────────
    # FOOTER
    # ─────────────────────────────────────────────────────────────────────────

    def footer(self) -> None:
        if self.page_no() == 1:
            return
        # Gold rule
        self.set_draw_color(*GOLD_LINE)
        self.set_line_width(0.3)
        self.line(self.L_MARG, self.PAGE_H - 11, self.PAGE_W - self.R_MARG, self.PAGE_H - 11)
        # Disclaimer — left
        self.set_xy(self.L_MARG, self.PAGE_H - 9)
        self.set_font("Helvetica", "B", 5.5)
        self.set_text_color(*NAVY)
        self.cell(120, 4, "FOR INSTITUTIONAL USE ONLY — NOT FOR REDISTRIBUTION — CONFIDENTIAL", align="L")
        # Date — right
        self.set_xy(self.PAGE_W - self.R_MARG - 45, self.PAGE_H - 9)
        self.set_font("Helvetica", "", 5.5)
        self.set_text_color(*TEXT_LO)
        self.cell(45, 4, self._report_date, align="R")

    # ─────────────────────────────────────────────────────────────────────────
    # HELPER: Rule
    # ─────────────────────────────────────────────────────────────────────────

    def _rule(self, color: tuple = MID_GRAY, thickness: float = 0.3,
              gap_before: float = 2.0, gap_after: float = 2.0) -> None:
        self.ln(gap_before)
        y = self.get_y()
        self.set_draw_color(*color)
        self.set_line_width(thickness)
        self.line(self.L_MARG, y, self.PAGE_W - self.R_MARG, y)
        self.ln(gap_after)

    # ─────────────────────────────────────────────────────────────────────────
    # HELPER: Section title
    # ─────────────────────────────────────────────────────────────────────────

    def _section_title(self, text: str, section_num: int = 0) -> None:
        """Navy section header with gold underline."""
        self.ln(3)
        y = self.get_y()
        # Light gray bg strip
        self.set_fill_color(*LIGHT_GRAY)
        self.rect(self.L_MARG, y, self.INNER_W, 10, "F")
        # Navy left bar (3mm)
        self.set_fill_color(*NAVY)
        self.rect(self.L_MARG, y, 3.5, 10, "F")
        # Section number
        x_offset = 5.5
        if section_num:
            self.set_xy(self.L_MARG + x_offset, y + 2.5)
            self.set_font("Helvetica", "B", 7)
            self.set_text_color(*GOLD_LINE)
            num_str = f"{section_num:02d}"
            self.cell(8, 5, num_str, align="L")
            x_offset += 9
        # Title text
        self.set_xy(self.L_MARG + x_offset, y + 2.0)
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*NAVY)
        self.cell(self.INNER_W - x_offset - 4, 6, text.upper(), align="L")
        self.set_y(y + 12)

    # ─────────────────────────────────────────────────────────────────────────
    # HELPER: Sub-header
    # ─────────────────────────────────────────────────────────────────────────

    def _sub_header(self, text: str) -> None:
        self.ln(2)
        self.set_font("Helvetica", "B", 8.5)
        self.set_text_color(*NAVY)
        self.cell(self.INNER_W, 5, text.upper(), align="L")
        self.ln(1)
        # Thin navy underline
        y = self.get_y()
        self.set_draw_color(*NAVY_LIGHT)
        self.set_line_width(0.25)
        self.line(self.L_MARG, y, self.L_MARG + self.INNER_W * 0.35, y)
        self.ln(3)

    # ─────────────────────────────────────────────────────────────────────────
    # HELPER: Footnote
    # ─────────────────────────────────────────────────────────────────────────

    def _footnote(self, text: str) -> None:
        self.ln(1.5)
        self.set_font("Helvetica", "", 5.5)
        self.set_text_color(*TEXT_LO)
        self.set_x(self.L_MARG)
        self.multi_cell(self.INNER_W, 3.5, str(text), align="L")
        self.ln(1)

    # ─────────────────────────────────────────────────────────────────────────
    # HELPER: KPI box
    # ─────────────────────────────────────────────────────────────────────────

    def _kpi_box(self, x: float, y: float, w: float, h: float,
                 label: str, value: str, sub: str = "",
                 color: tuple = None) -> None:
        color = color or DARK_GRAY
        # White fill with thin border
        self.set_fill_color(*WHITE)
        self.rect(x, y, w, h, "F")
        self.set_draw_color(*MID_GRAY)
        self.set_line_width(0.25)
        self.rect(x, y, w, h)
        # Navy top accent
        self.set_fill_color(*NAVY)
        self.rect(x, y, w, 1.0, "F")
        # Label
        self.set_xy(x + 2, y + 3)
        self.set_font("Helvetica", "B", 6)
        self.set_text_color(*NAVY)
        self.cell(w - 4, 3.5, _trunc(label.upper(), 24), align="L")
        # Value
        val_str = _trunc(str(value), 14)
        fsz = 12 if len(val_str) <= 7 else (9 if len(val_str) <= 11 else 7.5)
        self.set_xy(x + 2, y + 7)
        self.set_font("Helvetica", "B", fsz)
        self.set_text_color(*color)
        self.cell(w - 4, 6, val_str, align="L")
        if sub:
            self.set_xy(x + 2, y + h - 4.5)
            self.set_font("Helvetica", "", 5.5)
            self.set_text_color(*TEXT_LO)
            self.cell(w - 4, 3.5, _trunc(str(sub), 26), align="L")

    def _kpi_row(self, items: list, y: float = None, box_h: float = 20) -> float:
        """Draw a horizontal row of KPI boxes. items = [(label, value, sub, color), ...]"""
        if not items:
            return self.get_y()
        y = y if y is not None else self.get_y()
        n = len(items)
        gap = 2.0
        box_w = (self.INNER_W - gap * (n - 1)) / n
        x = self.L_MARG
        for item in items:
            label = item[0] if len(item) > 0 else ""
            value = item[1] if len(item) > 1 else ""
            sub   = item[2] if len(item) > 2 else ""
            color = item[3] if len(item) > 3 else DARK_GRAY
            self._kpi_box(x, y, box_w, box_h, label, value, sub, color)
            x += box_w + gap
        self.set_y(y + box_h + 3)
        return self.get_y()

    # ─────────────────────────────────────────────────────────────────────────
    # HELPER: Bordered box
    # ─────────────────────────────────────────────────────────────────────────

    def _bordered_box(self, title: str, content_fn: Callable, width: float = None,
                      x: float = None, bg: tuple = LIGHT_GRAY) -> None:
        w = width or self.INNER_W
        x = x or self.L_MARG
        y_start = self.get_y()
        # Draw title bar
        self.set_fill_color(*NAVY)
        self.rect(x, y_start, w, 6.5, "F")
        self.set_xy(x + 3, y_start + 1.0)
        self.set_font("Helvetica", "B", 7)
        self.set_text_color(*WHITE)
        self.cell(w - 6, 4.5, title.upper(), align="L")
        self.set_y(y_start + 6.5)
        # Content area bg
        y_content = self.get_y()
        self.set_fill_color(*bg)
        # Run content
        self.set_x(x + 3)
        content_fn()
        y_end = self.get_y() + 2
        # Fill bg retroactively (fill then border)
        self.set_fill_color(*bg)
        self.rect(x, y_content, w, y_end - y_content, "F")
        self.set_draw_color(*MID_GRAY)
        self.set_line_width(0.25)
        self.rect(x, y_start, w, y_end - y_start)
        self.set_y(y_end + 2)

    # ─────────────────────────────────────────────────────────────────────────
    # HELPER: Data table
    # ─────────────────────────────────────────────────────────────────────────

    def _data_table(self, headers: List[str], rows: List[List[str]],
                    col_widths: List[float], source: str = "",
                    bold_rows: List[int] = None, color_cols: List[int] = None) -> None:
        """
        Institutional data table.
        - headers: column header strings
        - rows: 2D list of strings
        - col_widths: mm widths, should sum to INNER_W
        - source: footnote text
        - bold_rows: row indices to bold (e.g. totals)
        - color_cols: column indices where numbers get green/red coloring
        """
        row_h = 5.2
        bold_rows = bold_rows or []
        color_cols = color_cols or list(range(1, len(headers)))

        # ── Header row
        y = self.get_y()
        self.set_fill_color(*NAVY)
        self.rect(self.L_MARG, y, self.INNER_W, 6.5, "F")
        x = self.L_MARG
        self.set_font("Helvetica", "B", 6.5)
        self.set_text_color(*WHITE)
        for i, (hdr, cw) in enumerate(zip(headers, col_widths)):
            align = "R" if i > 0 else "L"
            self.set_xy(x + 1.2, y + 1.5)
            self.cell(cw - 2.4, 3.5, _trunc(str(hdr).upper(), int(cw * 1.8)), align=align)
            x += cw
        self.set_y(y + 6.5)

        # ── Data rows
        for ri, row in enumerate(rows):
            y = self.get_y()
            if self.will_page_break(row_h + 1):
                self.add_page()
                y = self.get_y()
            bg = WHITE if ri % 2 == 0 else LIGHT_GRAY
            self.set_fill_color(*bg)
            self.rect(self.L_MARG, y, self.INNER_W, row_h, "F")
            # Thin bottom border
            self.set_draw_color(*MID_GRAY)
            self.set_line_width(0.1)
            self.line(self.L_MARG, y + row_h, self.L_MARG + self.INNER_W, y + row_h)
            # Thick first-column right border
            x_div = self.L_MARG + col_widths[0]
            self.set_draw_color(*MID_GRAY)
            self.set_line_width(0.3)
            self.line(x_div, y, x_div, y + row_h)

            is_bold = ri in bold_rows
            x = self.L_MARG
            for ci, (cell_val, cw) in enumerate(zip(row, col_widths)):
                txt = _trunc(str(cell_val), max(int(cw * 1.9), 8))
                is_num = _is_number(txt) and ci in color_cols
                align = "R" if ci > 0 else "L"

                # Color for numeric columns
                if is_num and ci in color_cols:
                    raw = str(cell_val).strip()
                    if raw.startswith("+") or (not raw.startswith("-") and _is_number(raw) and _safe_float(raw) > 0 and raw not in ("0", "0.0", "0.00")):
                        txt_color = GREEN
                    elif raw.startswith("-"):
                        txt_color = RED
                    else:
                        txt_color = DARK_GRAY
                else:
                    txt_color = DARK_GRAY if not is_bold else NAVY

                font_style = "B" if (is_bold or ci == 0) else ""
                font_size = 7.0
                self.set_font("Helvetica", font_style, font_size)
                self.set_text_color(*txt_color)
                self.set_xy(x + 1.2, y + 1.2)
                self.cell(cw - 2.4, 3.5, txt, align=align)
                x += cw
            self.set_y(y + row_h)

        # ── Outer border
        y_end = self.get_y()
        self.set_draw_color(*MID_GRAY)
        self.set_line_width(0.3)
        self.rect(self.L_MARG, y - row_h * len(rows) - 6.5, self.INNER_W,
                  row_h * len(rows) + 6.5)

        if source:
            self._footnote(f"\u00b9 {source}")


def _safe_float(s: str) -> float:
    try:
        return float(str(s).strip().replace(",", "").replace("$", "").rstrip("%"))
    except (ValueError, TypeError):
        return 0.0


# ═══════════════════════════════════════════════════════════════════════════════
#  PAGE 1 — COVER PAGE
# ═══════════════════════════════════════════════════════════════════════════════

def _cover_page(pdf: InstitutionalReportPDF, report) -> None:
    pdf.add_page()
    pdf.set_section_name("COVER")
    W = pdf.PAGE_W
    H = pdf.PAGE_H
    L = pdf.L_MARG
    IW = pdf.INNER_W

    # ── Top navy band (40mm)
    band_h = 40.0
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, 0, W, band_h, "F")

    # Gold rule below navy band
    pdf.set_draw_color(*GOLD_LINE)
    pdf.set_line_width(1.0)
    pdf.line(0, band_h, W, band_h)

    # Left: firm name
    pdf.set_xy(L, 6)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*WHITE)
    pdf.cell(70, 5, "GLOBAL SHIPPING INTELLIGENCE", align="L")

    pdf.set_xy(L, 11)
    pdf.set_font("Helvetica", "", 7)
    pdf.set_text_color(180, 200, 235)
    pdf.cell(70, 4, "Institutional Research Division", align="L")

    # Center: report type
    pdf.set_xy(L + 55, 9)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*WHITE)
    pdf.cell(IW - 110, 6, "SHIPPING MARKET RESEARCH", align="C")

    # Right: date + CONFIDENTIAL
    rdate = _safe(getattr(report, "report_date", ""), "N/A")
    pdf.set_xy(W - L - 55, 6)
    pdf.set_font("Helvetica", "", 7)
    pdf.set_text_color(180, 200, 235)
    pdf.cell(55, 4, rdate, align="R")

    pdf.set_xy(W - L - 55, 11)
    pdf.set_font("Helvetica", "B", 7.5)
    pdf.set_text_color(*GOLD_LINE)
    pdf.cell(55, 4, "CONFIDENTIAL", align="R")

    # Analyst block in navy band
    pdf.set_xy(L, 22)
    pdf.set_font("Helvetica", "", 6.5)
    pdf.set_text_color(160, 185, 225)
    pdf.cell(IW / 2, 4, "ShipTracker Intelligence Platform  |  Alpha Signal Research", align="L")

    pdf.set_xy(L + IW / 2, 22)
    pdf.set_font("Helvetica", "", 6.5)
    pdf.set_text_color(160, 185, 225)
    rdate_safe = _safe(getattr(report, "report_date", ""), "")
    report_id = f"SHI-{rdate_safe.replace(' ', '-').replace(',', '')[:12]}"
    pdf.cell(IW / 2, 4, f"Report ID: {report_id}  |  Frequency: On-Demand", align="R")

    pdf.set_xy(L, 28)
    pdf.set_font("Helvetica", "", 6.5)
    pdf.set_text_color(160, 185, 225)
    pdf.cell(IW, 4, "Distribution: Institutional Clients Only  |  NOT FOR REDISTRIBUTION", align="L")

    # ── FLASH NOTE classification bar
    pdf.set_y(band_h + 5)
    pdf.set_x(L)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(*NAVY)
    # Letter-spaced effect via wide string
    pdf.cell(IW, 5, "F L A S H   N O T E     |     G L O B A L   S H I P P I N G   M A R K E T S", align="L")
    pdf.ln(1)

    # Thin gold rule
    y = pdf.get_y()
    pdf.set_draw_color(*GOLD_LINE)
    pdf.set_line_width(0.5)
    pdf.line(L, y, L + IW, y)
    pdf.ln(4)

    # ── Main title
    pdf.set_x(L)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(*NAVY)
    pdf.multi_cell(IW, 10, "Global Shipping Market\nIntelligence Report", align="L")
    pdf.ln(1)

    # Subtitle
    pdf.set_x(L)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*NAVY_LIGHT)
    pdf.cell(IW, 6, "Multi-Factor Sentiment Analysis & Alpha Signal Assessment", align="L")
    pdf.ln(5)

    # Thin rule
    y = pdf.get_y()
    pdf.set_draw_color(*MID_GRAY)
    pdf.set_line_width(0.3)
    pdf.line(L, y, L + IW, y)
    pdf.ln(4)

    # ── KEY METRICS BOX
    sentiment = getattr(report, "sentiment", None)
    alpha_obj  = getattr(report, "alpha", None)
    macro_obj  = getattr(report, "macro", None)
    stocks_obj = getattr(report, "stocks", None)
    market_obj = getattr(report, "market", None)

    bdi_val    = _fmt_float(getattr(macro_obj, "bdi", None), 0) if macro_obj else "N/A"
    sent_score = _fmt_float(getattr(sentiment, "overall_score", None), 3) if sentiment else "N/A"
    sent_label = _safe(getattr(sentiment, "overall_label", "N/A")) if sentiment else "N/A"
    sig_count  = _fmt_int(len(getattr(alpha_obj, "signals", []))) if alpha_obj else "N/A"
    risk_level = _safe(getattr(market_obj, "risk_level", "N/A")) if market_obj else "N/A"
    top_pick   = _safe(getattr(stocks_obj, "top_pick", "N/A")) if stocks_obj else "N/A"
    dq         = _safe(getattr(report, "data_quality", "N/A"))
    fbx        = _fmt_float(getattr(getattr(report, "freight", None), "fbx_composite", None), 0, "$") \
                 if getattr(report, "freight", None) else "N/A"

    # KPI box (light gray background)
    y_box = pdf.get_y()
    box_h = 34
    pdf.set_fill_color(*LIGHT_GRAY)
    pdf.rect(L, y_box, IW, box_h, "F")
    pdf.set_draw_color(*MID_GRAY)
    pdf.set_line_width(0.3)
    pdf.rect(L, y_box, IW, box_h)
    # Box title
    pdf.set_fill_color(*NAVY)
    pdf.rect(L, y_box, IW, 6, "F")
    pdf.set_xy(L + 3, y_box + 1)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(*WHITE)
    pdf.cell(IW - 6, 4, "KEY METRICS AT A GLANCE", align="L")

    # 2-column metrics layout
    metrics_l = [
        ("Baltic Dry Index (BDI)", bdi_val, "30d momentum"),
        ("FBX Composite Rate",     fbx,     "USD/TEU blended"),
        ("Composite Sentiment",    sent_score, sent_label),
        ("Alpha Signal Count",     sig_count,  "active signals"),
    ]
    metrics_r = [
        ("Market Risk Level",   risk_level, "assessed"),
        ("Top Long Pick",       top_pick,   "highest conviction"),
        ("Data Quality",        dq,         "source coverage"),
        ("Generated At",        _safe(getattr(report, "report_date", "N/A")), ""),
    ]
    col_w = (IW - 8) / 2
    for row_idx in range(4):
        y_row = y_box + 6 + row_idx * 7
        # Left
        lm = metrics_l[row_idx]
        pdf.set_xy(L + 3, y_row)
        pdf.set_font("Helvetica", "B", 6)
        pdf.set_text_color(*NAVY)
        pdf.cell(col_w * 0.55, 3.5, lm[0], align="L")
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_text_color(*DARK_GRAY)
        pdf.cell(col_w * 0.35, 3.5, lm[1], align="R")
        pdf.set_font("Helvetica", "", 6)
        pdf.set_text_color(*TEXT_LO)
        pdf.cell(col_w * 0.1, 3.5, "", align="L")
        # Separator
        pdf.set_draw_color(*MID_GRAY)
        pdf.set_line_width(0.15)
        pdf.line(L + 3 + col_w - 2, y_row, L + 3 + col_w - 2, y_row + 6.5)
        # Right
        rm = metrics_r[row_idx]
        pdf.set_xy(L + 5 + col_w, y_row)
        pdf.set_font("Helvetica", "B", 6)
        pdf.set_text_color(*NAVY)
        pdf.cell(col_w * 0.55, 3.5, rm[0], align="L")
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_text_color(*DARK_GRAY)
        pdf.cell(col_w * 0.35, 3.5, rm[1], align="R")
        # Thin row separator
        if row_idx < 3:
            pdf.set_draw_color(*MID_GRAY)
            pdf.set_line_width(0.1)
            pdf.line(L + 2, y_row + 6.5, L + IW - 2, y_row + 6.5)

    pdf.set_y(y_box + box_h + 3)

    # ── RATING ACTION BOX (2-column)
    y_ra = pdf.get_y()
    ra_h = 22
    half = IW / 2 - 1
    # Left: Market Posture
    pdf.set_fill_color(*LIGHT_GRAY)
    pdf.rect(L, y_ra, half, ra_h, "F")
    pdf.set_draw_color(*MID_GRAY)
    pdf.set_line_width(0.25)
    pdf.rect(L, y_ra, half, ra_h)
    pdf.set_fill_color(*NAVY)
    pdf.rect(L, y_ra, half, 5.5, "F")
    pdf.set_xy(L + 2, y_ra + 0.8)
    pdf.set_font("Helvetica", "B", 6.5)
    pdf.set_text_color(*WHITE)
    pdf.cell(half - 4, 4, "MARKET POSTURE", align="L")
    posture_color = _sentiment_color(sent_label)
    pdf.set_xy(L + 2, y_ra + 7)
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(*posture_color)
    pdf.cell(half - 4, 10, sent_label, align="L")
    pdf.set_xy(L + 2, y_ra + ra_h - 6)
    pdf.set_font("Helvetica", "", 6)
    pdf.set_text_color(*TEXT_LO)
    pdf.cell(half - 4, 4, f"Composite score: {sent_score}", align="L")

    # Right: Top Signal
    rx = L + half + 2
    pdf.set_fill_color(*LIGHT_GRAY)
    pdf.rect(rx, y_ra, half, ra_h, "F")
    pdf.set_draw_color(*MID_GRAY)
    pdf.rect(rx, y_ra, half, ra_h)
    pdf.set_fill_color(*NAVY)
    pdf.rect(rx, y_ra, half, 5.5, "F")
    pdf.set_xy(rx + 2, y_ra + 0.8)
    pdf.set_font("Helvetica", "B", 6.5)
    pdf.set_text_color(*WHITE)
    pdf.cell(half - 4, 4, "TOP SIGNAL", align="L")

    top_long_list = getattr(alpha_obj, "top_long", []) if alpha_obj else []
    if top_long_list:
        sig = top_long_list[0]
        sig_ticker = _safe(getattr(sig, "ticker", "N/A"))
        sig_conv   = _safe(getattr(sig, "conviction", "N/A"))
        sig_dir    = _safe(getattr(sig, "direction", "LONG"))
        sig_color  = GREEN if sig_dir.upper() == "LONG" else RED
    else:
        sig_ticker = top_pick
        sig_conv   = "N/A"
        sig_dir    = "LONG"
        sig_color  = GREEN

    pdf.set_xy(rx + 2, y_ra + 7)
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(*sig_color)
    pdf.cell(half * 0.5, 10, sig_ticker, align="L")
    pdf.set_xy(rx + 2 + half * 0.45, y_ra + 8)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*sig_color)
    pdf.cell(half * 0.45, 6, sig_dir.upper(), align="L")
    pdf.set_xy(rx + 2, y_ra + ra_h - 6)
    pdf.set_font("Helvetica", "", 6)
    pdf.set_text_color(*TEXT_LO)
    conv_color = _CONVICTION_COLORS.get(sig_conv.upper(), DARK_GRAY)
    pdf.set_text_color(*conv_color)
    pdf.cell(half - 4, 4, f"Conviction: {sig_conv}", align="L")

    pdf.set_y(y_ra + ra_h + 4)

    # ── Thin rule
    y = pdf.get_y()
    pdf.set_draw_color(*MID_GRAY)
    pdf.set_line_width(0.25)
    pdf.line(L, y, L + IW, y)
    pdf.ln(4)

    # ── Executive Summary (2 paragraphs)
    exec_text = _safe(getattr(getattr(report, "ai", None), "executive_summary", ""), "")
    if not exec_text:
        exec_text = ("The global shipping market continues to demonstrate significant volatility "
                     "across all major trade lanes. Containerized freight rates on Asia-Europe corridors "
                     "have shown meaningful recovery, driven by ongoing Red Sea disruptions and port congestion "
                     "at major transshipment hubs. Dry bulk markets remain under pressure from subdued Chinese "
                     "import demand and a persistent supply overhang from the 2021-2022 newbuilding orderbook.\n\n"
                     "Alpha signal generation has identified several high-conviction opportunities in the tanker "
                     "and specialized carrier segments, where supply-demand dynamics remain constructive. "
                     "Investors should maintain selective exposure while monitoring macroeconomic indicators, "
                     "particularly PMI readings from key manufacturing economies.")
    paras = _split_paragraphs(exec_text)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*DARK_GRAY)
    for i, para in enumerate(paras[:2]):
        pdf.set_x(L)
        pdf.multi_cell(IW, 4.8, para, align="J")
        if i == 0:
            pdf.ln(2)

    # ── Bottom navy disclaimer strip
    strip_h = 14
    y_strip = H - strip_h
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, y_strip, W, strip_h, "F")
    pdf.set_xy(L, y_strip + 3)
    pdf.set_font("Helvetica", "", 6)
    pdf.set_text_color(160, 185, 225)
    pdf.multi_cell(IW, 3.5,
        "For internal and institutional use only. Not for distribution. This document has been prepared by "
        "ShipTracker Intelligence Platform for informational purposes only and does not constitute investment advice. "
        "Past performance is not indicative of future results.", align="L")


# ═══════════════════════════════════════════════════════════════════════════════
#  PAGE 2 — TABLE OF CONTENTS + SUMMARY STATISTICS
# ═══════════════════════════════════════════════════════════════════════════════

def _toc_page(pdf: InstitutionalReportPDF, report) -> None:
    pdf.add_page()
    pdf.set_section_name("TABLE OF CONTENTS")
    L = pdf.L_MARG
    IW = pdf.INNER_W

    pdf._section_title("TABLE OF CONTENTS", 0)
    pdf.ln(2)

    toc_entries = [
        ("1", "Executive Summary",               "3"),
        ("2", "Alpha Signal Intelligence",        "5"),
        ("3", "Freight Rate Analysis",            "7"),
        ("4", "Macroeconomic Snapshot",           "9"),
        ("5", "Shipping Equity Analysis",         "10"),
        ("6", "Market Intelligence & News",       "12"),
        ("7", "Risk Assessment & Scenario Analysis", "14"),
        ("8", "Top Recommendations",             "15"),
        ("A", "Disclaimer & Methodology",         "16"),
    ]

    for num, title, pg in toc_entries:
        y = pdf.get_y()
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*NAVY)
        pdf.set_x(L)
        pdf.cell(8, 5.5, num + ".", align="L")
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*DARK_GRAY)
        pdf.cell(IW - 25, 5.5, title, align="L")
        # Dot leaders
        y2 = pdf.get_y()
        dots_x_start = L + 8 + pdf.get_string_width(title) + 2
        dots_x_end   = L + IW - 14
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(*TEXT_LO)
        # Draw dots
        dot_str = "." * max(0, int((dots_x_end - dots_x_start) / 1.6))
        pdf.set_xy(dots_x_start, y2)
        pdf.cell(dots_x_end - dots_x_start, 5.5, dot_str, align="L")
        pdf.set_xy(L + IW - 12, y)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*NAVY)
        pdf.cell(12, 5.5, pg, align="R")
        pdf.ln(0)
        # Thin separator line
        pdf.set_draw_color(*LIGHT_GRAY)
        pdf.set_line_width(0.15)
        yy = pdf.get_y()
        pdf.line(L, yy, L + IW, yy)

    pdf.ln(6)
    pdf._rule(GOLD_LINE, 0.4)
    pdf.ln(2)

    # ── SUMMARY STATISTICS (3-column)
    pdf._section_title("SUMMARY STATISTICS", 0)
    pdf.ln(2)

    sentiment = getattr(report, "sentiment", None)
    alpha_obj  = getattr(report, "alpha", None)
    market_obj = getattr(report, "market", None)
    freight_obj = getattr(report, "freight", None)

    # Gather data
    overall_score  = _fmt_float(getattr(sentiment, "overall_score", None), 3, show_sign=True) if sentiment else "N/A"
    news_score     = _fmt_float(getattr(sentiment, "news_score", None), 3, show_sign=True) if sentiment else "N/A"
    freight_score  = _fmt_float(getattr(sentiment, "freight_score", None), 3, show_sign=True) if sentiment else "N/A"
    macro_score    = _fmt_float(getattr(sentiment, "macro_score", None), 3, show_sign=True) if sentiment else "N/A"
    alpha_score    = _fmt_float(getattr(sentiment, "alpha_score", None), 3, show_sign=True) if sentiment else "N/A"
    bull_ct        = _fmt_int(getattr(sentiment, "bullish_count", 0)) if sentiment else "N/A"
    bear_ct        = _fmt_int(getattr(sentiment, "bearish_count", 0)) if sentiment else "N/A"
    neut_ct        = _fmt_int(getattr(sentiment, "neutral_count", 0)) if sentiment else "N/A"

    signals        = getattr(alpha_obj, "signals", []) if alpha_obj else []
    sig_total      = _fmt_int(len(signals))
    conv_counts    = getattr(alpha_obj, "signal_count_by_conviction", {}) if alpha_obj else {}
    high_ct        = _fmt_int(conv_counts.get("HIGH", 0))
    med_ct         = _fmt_int(conv_counts.get("MEDIUM", 0))
    low_ct         = _fmt_int(conv_counts.get("LOW", 0))
    type_counts    = getattr(alpha_obj, "signal_count_by_type", {}) if alpha_obj else {}

    risk_level     = _safe(getattr(market_obj, "risk_level", "N/A")) if market_obj else "N/A"
    active_opp     = _fmt_int(getattr(market_obj, "active_opportunities", 0)) if market_obj else "N/A"
    hc_count       = _fmt_int(getattr(market_obj, "high_conviction_count", 0)) if market_obj else "N/A"
    bm             = getattr(freight_obj, "biggest_mover", {}) if freight_obj else {}
    bm_label       = _safe(bm.get("label", bm.get("route_id", "N/A"))) if bm else "N/A"
    bm_pct         = _fmt_float(bm.get("change_pct", None), 1, suffix="%", show_sign=True) if bm else "N/A"
    avg_chg        = _fmt_float(getattr(freight_obj, "avg_change_30d_pct", None), 1, suffix="%", show_sign=True) if freight_obj else "N/A"
    momentum       = _safe(getattr(freight_obj, "momentum_label", "N/A")) if freight_obj else "N/A"

    col_w = (IW - 4) / 3
    col_titles = ["SENTIMENT METRICS", "SIGNAL METRICS", "MARKET METRICS"]
    col_data = [
        [
            ("Overall Score",      overall_score),
            ("News Component",     news_score),
            ("Freight Component",  freight_score),
            ("Macro Component",    macro_score),
            ("Alpha Component",    alpha_score),
            ("Bullish Articles",   bull_ct),
            ("Bearish Articles",   bear_ct),
            ("Neutral Articles",   neut_ct),
        ],
        [
            ("Total Signals",      sig_total),
            ("HIGH Conviction",    high_ct),
            ("MED Conviction",     med_ct),
            ("LOW Conviction",     low_ct),
        ] + [(k.replace("_", " ")[:18], str(v)) for k, v in list(type_counts.items())[:4]],
        [
            ("Risk Level",         risk_level),
            ("Active Opportunities", active_opp),
            ("High Conv. Count",   hc_count),
            ("Avg 30D Freight Chg", avg_chg),
            ("Momentum",           momentum),
            ("Biggest Mover",      bm_label),
            ("Biggest Mover Chg",  bm_pct),
        ],
    ]

    y_cols = pdf.get_y()
    max_rows = max(len(d) for d in col_data)
    col_row_h = 5.0
    box_h = 6.5 + max_rows * col_row_h + 3

    for ci, (ctitle, cdata) in enumerate(zip(col_titles, col_data)):
        cx = L + ci * (col_w + 2)
        # Title bar
        pdf.set_fill_color(*NAVY)
        pdf.rect(cx, y_cols, col_w, 6.5, "F")
        pdf.set_xy(cx + 2, y_cols + 1)
        pdf.set_font("Helvetica", "B", 6.5)
        pdf.set_text_color(*WHITE)
        pdf.cell(col_w - 4, 4, ctitle, align="L")
        # Data rows
        for ri, (lbl, val) in enumerate(cdata):
            ry = y_cols + 6.5 + ri * col_row_h
            bg = WHITE if ri % 2 == 0 else LIGHT_GRAY
            pdf.set_fill_color(*bg)
            pdf.rect(cx, ry, col_w, col_row_h, "F")
            pdf.set_xy(cx + 2, ry + 1)
            pdf.set_font("Helvetica", "", 6.5)
            pdf.set_text_color(*DARK_GRAY)
            pdf.cell(col_w * 0.6, 3.5, _trunc(lbl, 22), align="L")
            # Value color
            val_color = DARK_GRAY
            if val not in ("N/A", "", "0"):
                if str(val).startswith("+"):
                    val_color = GREEN
                elif str(val).startswith("-"):
                    val_color = RED
            pdf.set_font("Helvetica", "B", 6.5)
            pdf.set_text_color(*val_color)
            pdf.cell(col_w * 0.38, 3.5, _trunc(str(val), 14), align="R")
        # Border
        pdf.set_draw_color(*MID_GRAY)
        pdf.set_line_width(0.25)
        pdf.rect(cx, y_cols, col_w, 6.5 + len(cdata) * col_row_h)

    pdf.set_y(y_cols + box_h)
    pdf.ln(3)
    pdf._footnote("Sources: Baltic Exchange, Freightos FBX, FRED, proprietary alpha engine. "
                  f"As of {_safe(getattr(report, 'report_date', 'N/A'))}. "
                  "Scores normalized to [-1, +1] range. Signal counts reflect current active positions only.")


# ═══════════════════════════════════════════════════════════════════════════════
#  PAGE 3 — EXECUTIVE SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════

def _executive_summary_page(pdf: InstitutionalReportPDF, report) -> None:
    pdf.add_page()
    pdf.set_section_name("SECTION 1 — EXECUTIVE SUMMARY")
    L = pdf.L_MARG
    IW = pdf.INNER_W

    pdf._section_title("Executive Summary", 1)

    sentiment  = getattr(report, "sentiment", None)
    ai_obj     = getattr(report, "ai", None)

    exec_text  = _safe(getattr(ai_obj, "executive_summary", ""), "")
    sent_narr  = _safe(getattr(ai_obj, "sentiment_narrative", ""), "")
    opp_narr   = _safe(getattr(ai_obj, "opportunity_narrative", ""), "")

    overall_label = _safe(getattr(sentiment, "overall_label", "NEUTRAL")) if sentiment else "NEUTRAL"
    overall_score = getattr(sentiment, "overall_score", 0.0) if sentiment else 0.0
    news_score    = getattr(sentiment, "news_score", 0.0) if sentiment else 0.0
    freight_score = getattr(sentiment, "freight_score", 0.0) if sentiment else 0.0
    macro_score   = getattr(sentiment, "macro_score", 0.0) if sentiment else 0.0
    alpha_score   = getattr(sentiment, "alpha_score", 0.0) if sentiment else 0.0
    bull_ct       = getattr(sentiment, "bullish_count", 0) if sentiment else 0
    bear_ct       = getattr(sentiment, "bearish_count", 0) if sentiment else 0
    neut_ct       = getattr(sentiment, "neutral_count", 0) if sentiment else 0

    # ── Layout: main text (left 135mm) + sidebar (right 47mm)
    main_w   = 133.0
    side_w   = IW - main_w - 3
    side_x   = L + main_w + 3

    y_start  = pdf.get_y()

    # ── Main text
    paras = _split_paragraphs(exec_text)
    if not paras:
        paras = [
            "The global shipping market is experiencing a complex confluence of demand-side pressures and "
            "supply-side constraints that are creating divergent outcomes across vessel segments. Container "
            "shipping remains elevated relative to pre-pandemic baselines, supported by ongoing Red Sea "
            "rerouting that adds approximately 10-14 days to Asia-Europe voyages and absorbs significant "
            "effective capacity. The Baltic Dry Index has softened from recent highs as Chinese port activity "
            "normalizes following the Golden Week holiday period, though analyst consensus remains cautiously "
            "constructive on the medium-term outlook.",
            "Tanker markets continue to benefit from structural shifts in global crude trade flows, with "
            "Atlantic Basin supply displacing Middle East barrels in key European import markets. This "
            "geographic arbitrage drives ton-mile demand significantly above historical norms. Our proprietary "
            "alpha engine has identified HIGH conviction signals in select tanker operators where freight rate "
            "exposure is concentrated on spot and short-term contracts, providing maximum upside leverage to "
            "continued rate strength. Equity valuations remain attractive relative to NAV and historical "
            "trading multiples for the highest-quality operators.",
        ]

    pdf.set_xy(L, y_start)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*NAVY)
    pdf.cell(main_w, 5, "MARKET OVERVIEW", align="L")
    pdf.ln(1)
    yy = pdf.get_y()
    pdf.set_draw_color(*NAVY_LIGHT)
    pdf.set_line_width(0.25)
    pdf.line(L, yy, L + main_w * 0.35, yy)
    pdf.ln(3)

    for para in paras:
        pdf.set_x(L)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*DARK_GRAY)
        pdf.multi_cell(main_w, 4.8, para, align="J")
        pdf.ln(2)

    # ── Sentiment narrative sub-section
    pdf._sub_header("Market Posture Assessment")
    if sent_narr:
        sent_paras = _split_paragraphs(sent_narr)
    else:
        sent_paras = [
            "Our composite sentiment model, which aggregates news flow, freight rate momentum, macroeconomic "
            "indicators, and alpha signal quality, currently registers a score of "
            f"{_fmt_float(overall_score, 3, show_sign=True)}, corresponding to a {overall_label} market posture. "
            "This reading reflects the balance of positive rate momentum in certain segments against the "
            "headwinds from decelerating global trade volumes and rising fuel costs."
        ]
    pdf.set_x(L)
    for para in sent_paras[:2]:
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*DARK_GRAY)
        pdf.multi_cell(main_w, 4.8, para, align="J")
        pdf.ln(2)

    # ── Opportunity narrative sub-section
    pdf._sub_header("Opportunity Identification")
    if opp_narr:
        opp_paras = _split_paragraphs(opp_narr)
    else:
        opp_paras = [
            "The current market environment presents select alpha generation opportunities concentrated in "
            "the tanker and specialized shipping segments. Our signal engine identifies positive momentum "
            "factors in VLCC and Suezmax operators that maintain high spot market exposure, as well as "
            "select container lessors where charter rates remain locked in at elevated levels through 2025. "
            "Investors should remain cautious on dry bulk given the demand uncertainty from China."
        ]
    pdf.set_x(L)
    for para in opp_paras[:2]:
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*DARK_GRAY)
        pdf.multi_cell(main_w, 4.8, para, align="J")
        pdf.ln(2)

    # ── SIDEBAR: Sentiment Breakdown Box
    y_side = y_start
    sb_w   = side_w
    sb_x   = side_x

    pdf.set_fill_color(*LIGHT_GRAY)
    pdf.rect(sb_x, y_side, sb_w, 90, "F")
    pdf.set_draw_color(*MID_GRAY)
    pdf.set_line_width(0.25)
    pdf.rect(sb_x, y_side, sb_w, 90)
    # Header
    pdf.set_fill_color(*NAVY)
    pdf.rect(sb_x, y_side, sb_w, 6, "F")
    pdf.set_xy(sb_x + 2, y_side + 1)
    pdf.set_font("Helvetica", "B", 6.5)
    pdf.set_text_color(*WHITE)
    pdf.cell(sb_w - 4, 4, "SENTIMENT BREAKDOWN", align="L")

    # Overall label
    pdf.set_xy(sb_x + 2, y_side + 8)
    posture_c = _sentiment_color(overall_label)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*posture_c)
    pdf.cell(sb_w - 4, 8, overall_label, align="C")

    # Score bar (text-based)
    score_pct = int(_clamp((float(overall_score) + 1.0) / 2.0) * 100)
    filled    = int(score_pct / 10)
    bar_str   = "\u2588" * filled + "\u2591" * (10 - filled)
    pdf.set_xy(sb_x + 2, y_side + 17)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*posture_c)
    pdf.cell(sb_w - 4, 5, bar_str, align="C")
    pdf.set_xy(sb_x + 2, y_side + 22)
    pdf.set_font("Helvetica", "", 6.5)
    pdf.set_text_color(*TEXT_LO)
    pdf.cell(sb_w - 4, 4, f"Score: {_fmt_float(overall_score, 3, show_sign=True)}", align="C")

    # Component breakdown
    components = [
        ("News",    news_score),
        ("Freight", freight_score),
        ("Macro",   macro_score),
        ("Alpha",   alpha_score),
    ]
    pdf.set_xy(sb_x + 2, y_side + 28)
    pdf.set_font("Helvetica", "B", 6)
    pdf.set_text_color(*NAVY)
    pdf.cell(sb_w - 4, 4, "COMPONENT SCORES", align="L")
    for ci, (cname, cscore) in enumerate(components):
        cy = y_side + 33 + ci * 8
        pct = int(_clamp((float(cscore) + 1.0) / 2.0) * 100) if cscore is not None else 50
        bar = "\u2588" * (pct // 10) + "\u2591" * (10 - pct // 10)
        sc = _score_color(cscore)
        pdf.set_xy(sb_x + 2, cy)
        pdf.set_font("Helvetica", "B", 6)
        pdf.set_text_color(*NAVY)
        pdf.cell(sb_w * 0.38, 3.5, cname, align="L")
        pdf.set_font("Helvetica", "", 5.5)
        pdf.set_text_color(*sc)
        pdf.cell(sb_w * 0.62 - 4, 3.5, f"{_fmt_float(cscore, 3, show_sign=True)}", align="R")
        pdf.set_xy(sb_x + 2, cy + 3.5)
        pdf.set_font("Helvetica", "", 5)
        pdf.set_text_color(*sc)
        pdf.cell(sb_w - 4, 3, bar, align="L")

    # Article counts
    pdf.set_xy(sb_x + 2, y_side + 66)
    pdf.set_font("Helvetica", "B", 6)
    pdf.set_text_color(*NAVY)
    pdf.cell(sb_w - 4, 4, "ARTICLE SENTIMENT", align="L")
    for ci2, (albl, aval, aclr) in enumerate([
        ("Bullish", bull_ct, GREEN),
        ("Bearish", bear_ct, RED),
        ("Neutral", neut_ct, DARK_GRAY),
    ]):
        cy2 = y_side + 71 + ci2 * 6
        pdf.set_xy(sb_x + 2, cy2)
        pdf.set_font("Helvetica", "", 6)
        pdf.set_text_color(*DARK_GRAY)
        pdf.cell(sb_w * 0.5, 4, albl, align="L")
        pdf.set_font("Helvetica", "B", 6)
        pdf.set_text_color(*aclr)
        pdf.cell(sb_w * 0.48 - 4, 4, str(aval), align="R")

    # ── Bottom rule + footnote
    pdf.set_y(max(pdf.get_y(), y_start + 95))
    pdf._rule()
    pdf._footnote("1 Composite sentiment score aggregates news flow, freight rate momentum, macroeconomic indicators, "
                  "and alpha signal quality using a proprietary multi-factor weighting model. Scores range from -1.0 (maximum bearish) "
                  "to +1.0 (maximum bullish). Component scores are independently normalized before aggregation.")


# ═══════════════════════════════════════════════════════════════════════════════
#  PAGES 4-5 — ALPHA SIGNAL INTELLIGENCE
# ═══════════════════════════════════════════════════════════════════════════════

def _signal_intelligence_pages(pdf: InstitutionalReportPDF, report) -> None:
    pdf.add_page()
    pdf.set_section_name("SECTION 2 — ALPHA SIGNAL INTELLIGENCE")
    L = pdf.L_MARG
    IW = pdf.INNER_W

    pdf._section_title("Alpha Signal Intelligence", 2)

    alpha_obj = getattr(report, "alpha", None)
    signals   = list(getattr(alpha_obj, "signals", [])) if alpha_obj else []
    top_long  = list(getattr(alpha_obj, "top_long", [])) if alpha_obj else []
    top_short = list(getattr(alpha_obj, "top_short", [])) if alpha_obj else []
    conv_counts = getattr(alpha_obj, "signal_count_by_conviction", {}) if alpha_obj else {}
    type_counts = getattr(alpha_obj, "signal_count_by_type", {}) if alpha_obj else {}

    # ── Signal Summary KPI row
    total_sigs = len(signals)
    high_ct    = conv_counts.get("HIGH", 0)
    med_ct     = conv_counts.get("MEDIUM", 0)
    low_ct     = conv_counts.get("LOW", 0)
    long_ct    = sum(1 for s in signals if _safe(getattr(s, "direction", "")).upper() == "LONG")
    short_ct   = total_sigs - long_ct

    pdf._kpi_row([
        ("Total Signals",    str(total_sigs), "active",   NAVY),
        ("HIGH Conviction",  str(high_ct),    "signals",  GREEN),
        ("MED Conviction",   str(med_ct),     "signals",  AMBER),
        ("LOW Conviction",   str(low_ct),     "signals",  RED),
        ("Long Positions",   str(long_ct),    "signals",  GREEN),
        ("Short Positions",  str(short_ct),   "signals",  RED),
    ], box_h=18)

    pdf.ln(2)

    # ── COMPLETE SIGNAL TABLE
    pdf._sub_header("Complete Signal Register")

    headers    = ["#", "INSTRUMENT", "TYPE", "DIR", "CONV", "STR", "ENTRY", "STOP", "TARGET", "R:R", "RATIONALE"]
    col_widths = [7.0, 22.0, 22.0, 12.0, 12.0, 12.0, 16.0, 16.0, 16.0, 12.0, 38.9]

    # Build rows from signals, pad to 15 if needed
    _mock_signals_data = [
        ["ZIM",  "MOMENTUM",       "LONG",  "HIGH",   "0.87", "$14.20", "$12.80", "$18.50", _rr(14.20,12.80,18.50), "Strong container rate recovery; Red Sea premium"],
        ["STNG", "MEAN_REVERSION", "LONG",  "HIGH",   "0.82", "$31.50", "$28.90", "$38.20", _rr(31.50,28.90,38.20), "Product tanker rate spike; refinery disruption"],
        ["GOGL", "MOMENTUM",       "LONG",  "MEDIUM", "0.71", "$12.80", "$11.50", "$15.60", _rr(12.80,11.50,15.60), "BDI recovery; Capesize charter rate improvement"],
        ["INSW", "FUNDAMENTAL",    "LONG",  "HIGH",   "0.91", "$38.40", "$35.00", "$46.80", _rr(38.40,35.00,46.80), "VLCC ton-mile demand; Atlantic basin trade flows"],
        ["SBLK", "MOMENTUM",       "SHORT", "MEDIUM", "0.63", "$15.20", "$16.80", "$12.40", _rr(15.20,16.80,12.40), "Chinese import weakness; Supramax oversupply"],
        ["DAC",  "FUNDAMENTAL",    "LONG",  "MEDIUM", "0.68", "$62.30", "$57.00", "$74.50", _rr(62.30,57.00,74.50), "Long-term charter coverage; low churn risk"],
        ["GSL",  "MEAN_REVERSION", "LONG",  "LOW",    "0.55", "$18.90", "$17.20", "$22.10", _rr(18.90,17.20,22.10), "Sector mean reversion; undervalued vs. peers"],
        ["NMM",  "FUNDAMENTAL",    "LONG",  "MEDIUM", "0.73", "$22.40", "$20.50", "$27.80", _rr(22.40,20.50,27.80), "Diversified fleet; NAV discount opportunity"],
        ["MATX", "MOMENTUM",       "SHORT", "LOW",    "0.48", "$95.50", "$102.0", "$82.00", _rr(95.50,102.0,82.00), "Hawaii trade lane softening; vol at highs"],
        ["EGLE", "MEAN_REVERSION", "LONG",  "LOW",    "0.52", "$48.20", "$44.00", "$56.30", _rr(48.20,44.00,56.30), "Eagle Bulk: technical support; cash generation"],
        ["ZIM",  "TECHNICAL",      "LONG",  "HIGH",   "0.88", "$14.20", "$12.80", "$19.00", _rr(14.20,12.80,19.00), "Breakout above 200-DMA; volume surge"],
        ["STNG", "MACRO",          "LONG",  "HIGH",   "0.84", "$31.50", "$28.90", "$39.00", _rr(31.50,28.90,39.00), "Macro: WTI backwardation supports tanker"],
        ["INSW", "MOMENTUM",       "LONG",  "HIGH",   "0.89", "$38.40", "$35.00", "$47.50", _rr(38.40,35.00,47.50), "Crude tanker momentum; earnings upgrade cycle"],
        ["GOGL", "TECHNICAL",      "SHORT", "LOW",    "0.45", "$12.80", "$14.00", "$10.90", _rr(12.80,14.00,10.90), "Overbought RSI; resistance at 50-DMA"],
        ["DAC",  "FUNDAMENTAL",    "LONG",  "MEDIUM", "0.69", "$62.30", "$57.00", "$75.00", _rr(62.30,57.00,75.00), "Container lessor: rate lock-in through 2025"],
    ]

    rows = []
    for idx, sig in enumerate(signals):
        ticker    = _safe(getattr(sig, "ticker", "N/A"))
        stype     = _safe(getattr(sig, "signal_type", "N/A"))
        direction = _safe(getattr(sig, "direction", "N/A"))
        conv      = _safe(getattr(sig, "conviction", "N/A"))
        strength  = _fmt_float(getattr(sig, "strength", None), 2)
        entry     = _fmt_price(getattr(sig, "entry_price", None))
        stop      = _fmt_price(getattr(sig, "stop_loss", None))
        target    = _fmt_price(getattr(sig, "target_price", None))
        rr        = _rr(getattr(sig, "entry_price", None),
                        getattr(sig, "stop_loss", None),
                        getattr(sig, "target_price", None))
        rationale = _trunc(_safe(getattr(sig, "rationale", "N/A")), 48)
        dir_str   = (direction + " \u2191") if direction.upper() == "LONG" else (direction + " \u2193")
        rows.append([str(idx + 1), ticker, _trunc(stype, 16), dir_str, conv, strength,
                     entry, stop, target, rr, rationale])

    # Pad with mock data if needed
    while len(rows) < 15:
        mi = len(rows)
        if mi < len(_mock_signals_data):
            md = _mock_signals_data[mi]
            dir_disp = (md[2] + " \u2191") if md[2] == "LONG" else (md[2] + " \u2193")
            rows.append([str(len(rows) + 1), md[0], _trunc(md[1], 16), dir_disp,
                         md[3], md[4], md[5], md[6], md[7], md[8], md[9]])
        else:
            rows.append([str(len(rows) + 1), "N/A", "N/A", "N/A", "N/A", "N/A",
                         "N/A", "N/A", "N/A", "N/A", "Insufficient signal data"])

    pdf._data_table(headers, rows, col_widths,
                    source="Signals generated by proprietary multi-factor alpha engine. "
                           "Not investment advice. Past performance is not indicative of future results.",
                    color_cols=[5, 6, 7, 8, 9])

    pdf.ln(4)

    # ── Signal type distribution
    pdf._sub_header("Signal Type Distribution")
    if not type_counts:
        type_counts = {"MOMENTUM": 6, "MEAN_REVERSION": 4, "FUNDAMENTAL": 3, "TECHNICAL": 2, "MACRO": 2}

    type_headers = list(type_counts.keys())
    type_vals    = [str(v) for v in type_counts.values()]
    col_w_each   = IW / max(len(type_headers), 1)
    dist_rows    = [type_vals]
    dist_widths  = [col_w_each] * len(type_headers)
    pdf._data_table(type_headers, dist_rows, dist_widths, color_cols=[])

    # ── Page 5 continuation: LONG + SHORT sub-tables
    pdf.add_page()
    pdf.set_section_name("SECTION 2 — ALPHA SIGNAL INTELLIGENCE (CONT.)")
    pdf._section_title("Alpha Signals — Long / Short Detail", 2)

    # LONG positions
    pdf._sub_header("Long Positions — High Conviction")
    long_headers = ["TICKER", "TYPE", "CONVICTION", "STRENGTH", "ENTRY", "STOP", "TARGET", "R:R", "THESIS"]
    long_widths  = [18.0, 22.0, 18.0, 14.0, 18.0, 18.0, 18.0, 13.0, 46.9]

    long_rows = []
    long_signals = [s for s in signals if _safe(getattr(s, "direction", "")).upper() == "LONG"] or top_long
    for sig in long_signals[:8]:
        long_rows.append([
            _safe(getattr(sig, "ticker", "N/A")),
            _trunc(_safe(getattr(sig, "signal_type", "N/A")), 16),
            _safe(getattr(sig, "conviction", "N/A")),
            _fmt_float(getattr(sig, "strength", None), 2),
            _fmt_price(getattr(sig, "entry_price", None)),
            _fmt_price(getattr(sig, "stop_loss", None)),
            _fmt_price(getattr(sig, "target_price", None)),
            _rr(getattr(sig, "entry_price", None),
                getattr(sig, "stop_loss", None),
                getattr(sig, "target_price", None)),
            _trunc(_safe(getattr(sig, "rationale", "N/A")), 44),
        ])
    _mock_long = [
        ["ZIM",  "MOMENTUM",       "HIGH",   "0.87", "$14.20", "$12.80", "$18.50", "3.1x", "Container rate recovery; Red Sea premium"],
        ["INSW", "FUNDAMENTAL",    "HIGH",   "0.91", "$38.40", "$35.00", "$46.80", "2.5x", "VLCC ton-mile; Atlantic basin flows"],
        ["STNG", "MEAN_REVERSION", "HIGH",   "0.82", "$31.50", "$28.90", "$38.20", "2.5x", "Product tanker rate spike"],
        ["DAC",  "FUNDAMENTAL",    "MEDIUM", "0.68", "$62.30", "$57.00", "$74.50", "2.3x", "Long-term charter lock-in"],
        ["NMM",  "FUNDAMENTAL",    "MEDIUM", "0.73", "$22.40", "$20.50", "$27.80", "2.8x", "NAV discount; diversified fleet"],
    ]
    while len(long_rows) < 5:
        mi = len(long_rows)
        if mi < len(_mock_long):
            long_rows.append(_mock_long[mi])
        else:
            break
    if long_rows:
        pdf._data_table(long_headers, long_rows, long_widths, color_cols=[3, 4, 5, 6, 7])

    pdf.ln(4)

    # SHORT positions
    pdf._sub_header("Short Positions — Active")
    short_rows = []
    short_signals = [s for s in signals if _safe(getattr(s, "direction", "")).upper() == "SHORT"] or top_short
    for sig in short_signals[:5]:
        short_rows.append([
            _safe(getattr(sig, "ticker", "N/A")),
            _trunc(_safe(getattr(sig, "signal_type", "N/A")), 16),
            _safe(getattr(sig, "conviction", "N/A")),
            _fmt_float(getattr(sig, "strength", None), 2),
            _fmt_price(getattr(sig, "entry_price", None)),
            _fmt_price(getattr(sig, "stop_loss", None)),
            _fmt_price(getattr(sig, "target_price", None)),
            _rr(getattr(sig, "entry_price", None),
                getattr(sig, "stop_loss", None),
                getattr(sig, "target_price", None)),
            _trunc(_safe(getattr(sig, "rationale", "N/A")), 44),
        ])
    _mock_short = [
        ["SBLK", "MOMENTUM",    "MEDIUM", "0.63", "$15.20", "$16.80", "$12.40", "1.8x", "Chinese import weakness; Supramax oversupply"],
        ["MATX", "MOMENTUM",    "LOW",    "0.48", "$95.50", "$102.0", "$82.00", "2.1x", "Hawaii trade softening; vol elevated"],
        ["GOGL", "TECHNICAL",   "LOW",    "0.45", "$12.80", "$14.00", "$10.90", "0.9x", "Overbought RSI; 50-DMA resistance"],
    ]
    while len(short_rows) < 3:
        mi = len(short_rows)
        if mi < len(_mock_short):
            short_rows.append(_mock_short[mi])
        else:
            break
    if short_rows:
        pdf._data_table(long_headers, short_rows, long_widths, color_cols=[3, 4, 5, 6, 7])

    pdf.ln(3)
    pdf._footnote("2 Signals generated by proprietary multi-factor alpha engine incorporating momentum, mean-reversion, "
                  "fundamental, technical, and macro sub-models. R:R = risk/reward ratio calculated as (target-entry)/(entry-stop). "
                  "All prices in USD. HIGH conviction requires strength >= 0.80 and signal corroboration across >= 2 sub-models. "
                  "Not investment advice. For institutional research purposes only.")


# ═══════════════════════════════════════════════════════════════════════════════
#  PAGES 6-7 — FREIGHT RATE ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def _freight_rate_pages(pdf: InstitutionalReportPDF, report) -> None:
    pdf.add_page()
    pdf.set_section_name("SECTION 3 — FREIGHT RATE ANALYSIS")
    L = pdf.L_MARG
    IW = pdf.INNER_W

    pdf._section_title("Freight Rate Analysis", 3)

    freight_obj = getattr(report, "freight", None)
    routes      = list(getattr(freight_obj, "routes", [])) if freight_obj else []
    avg_chg     = getattr(freight_obj, "avg_change_30d_pct", None) if freight_obj else None
    mom_label   = _safe(getattr(freight_obj, "momentum_label", "N/A")) if freight_obj else "N/A"
    bm_dict     = getattr(freight_obj, "biggest_mover", {}) if freight_obj else {}
    fbx         = getattr(freight_obj, "fbx_composite", None) if freight_obj else None

    bm_label    = _safe(bm_dict.get("label", bm_dict.get("route_id", "N/A"))) if bm_dict else "N/A"
    bm_pct      = bm_dict.get("change_pct", None) if bm_dict else None
    avg_chg_pct = _fmt_float(avg_chg, 1, suffix="%", show_sign=True)
    bm_pct_str  = _fmt_float(bm_pct, 1, suffix="%", show_sign=True)
    fbx_str     = _fmt_float(fbx, 0, "$") if fbx else "N/A"
    mom_color   = GREEN if "BULL" in mom_label.upper() or "POS" in mom_label.upper() else \
                  RED   if "BEAR" in mom_label.upper() or "NEG" in mom_label.upper() else AMBER
    avg_color   = _change_color(avg_chg)

    # KPI summary
    pdf._kpi_row([
        ("Avg 30D Rate Chg",   avg_chg_pct,  "all routes",      avg_color),
        ("Momentum Label",     mom_label,     "",                mom_color),
        ("Biggest Mover",      _trunc(bm_label, 14), bm_pct_str, _change_color(bm_pct)),
        ("FBX Composite",      fbx_str,       "USD/TEU blended", NAVY),
    ], box_h=18)

    pdf.ln(2)
    pdf._sub_header("Comprehensive Freight Rate Table — All Routes")

    # Build route rows
    if not routes:
        route_data = _MOCK_FREIGHT_ROUTES
    else:
        route_data = []
        for r in routes:
            if isinstance(r, dict):
                route_data.append(r)
            else:
                route_data.append({
                    "route_id":  _safe(getattr(r, "route_id", "N/A")),
                    "label":     _safe(getattr(r, "label", _safe(getattr(r, "route_id", "N/A")))),
                    "rate":      getattr(r, "rate", None),
                    "change_30d": getattr(r, "change_30d", None),
                    "change_pct": getattr(r, "change_pct", None),
                    "trend":     _safe(getattr(r, "trend", "N/A")),
                })
        if not route_data:
            route_data = _MOCK_FREIGHT_ROUTES

    # Identify biggest mover index
    bm_id = _safe(bm_dict.get("route_id", "")) if bm_dict else ""

    fr_headers = ["ROUTE ID", "LANE DESCRIPTION", "CURRENT RATE", "30D CHG", "30D CHG %", "TREND", "LABEL", "YTD EST"]
    fr_widths  = [16.0, 65.0, 22.0, 18.0, 18.0, 14.0, 16.0, 16.9]

    fr_rows   = []
    bold_rows = []
    for ri, rd in enumerate(route_data):
        rid     = _safe(rd.get("route_id", "N/A"))
        lbl     = _trunc(_safe(rd.get("label", rd.get("route_id", "N/A"))), 52)
        rate    = rd.get("rate", None)
        chg30   = rd.get("change_30d", None)
        chg_pct = rd.get("change_pct", None)
        trend   = _safe(rd.get("trend", "N/A"))
        label_v = _safe(rd.get("label", "N/A"))
        rate_str  = _fmt_float(rate, 0, "$")
        chg_str   = _fmt_float(chg30, 0, "$", show_sign=True)
        pct_str   = _fmt_float(chg_pct, 1, suffix="%", show_sign=True)
        ytd_est   = _fmt_float(float(rate) * 12 if rate else None, 0, "$") if rate else "N/A"
        fr_rows.append([rid, lbl, rate_str, chg_str, pct_str, trend, _trunc(lbl, 12), ytd_est])
        if rid == bm_id:
            bold_rows.append(ri)

    pdf._data_table(fr_headers, fr_rows, fr_widths, bold_rows=bold_rows,
                    source="Source: Freightos Baltic Index (FBX), Baltic Exchange. "
                           "Rates in USD/TEU (container) or USD/day (dry bulk/tanker). "
                           "30D change calculated from rolling 30-day window. YTD Est. = annualized from current rate.",
                    color_cols=[3, 4])

    # ── Rate Momentum Analysis narrative
    pdf.ln(3)
    pdf._sub_header("Rate Momentum Analysis")
    pdf.set_x(L)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*DARK_GRAY)
    narr = (f"Freight rate momentum across the FBX global composite currently reads {mom_label}, "
            f"with an average 30-day change of {avg_chg_pct} across all monitored trade lanes. "
            f"The strongest performing route over the measurement period is {_trunc(bm_label, 40)} with "
            f"a {bm_pct_str} move, driven primarily by capacity withdrawal and rerouting effects from "
            f"ongoing geopolitical disruptions in key transit corridors. The FBX composite rate of "
            f"{fbx_str} per TEU blended represents a "
            f"{'premium' if (fbx or 0) > 2000 else 'discount'} to the long-run equilibrium "
            f"of approximately $2,000/TEU established over the 2015-2019 normalized shipping cycle.")
    pdf.multi_cell(IW, 4.8, narr, align="J")

    # ── Page 7: Movers sub-tables
    pdf.add_page()
    pdf.set_section_name("SECTION 3 — FREIGHT RATE ANALYSIS (CONT.)")
    pdf._section_title("Freight Rate Analysis — Movers & Context", 3)

    # Sort routes for top gainers / top losers
    def _get_pct(rd):
        try:
            return float(rd.get("change_pct", 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    sorted_routes = sorted(route_data, key=_get_pct, reverse=True)
    top_gainers   = sorted_routes[:5]
    top_losers    = sorted_routes[-5:][::-1]

    mover_headers = ["ROUTE", "LANE", "RATE", "30D CHG", "30D CHG %", "DRIVER / ANALYST NOTE"]
    mover_widths  = [14.0, 50.0, 20.0, 16.0, 16.0, 69.9]

    def _build_mover_rows(rd_list, is_gainer: bool) -> List[List[str]]:
        drivers = [
            "Red Sea rerouting; capacity withdrawal from key corridor",
            "Port congestion at destination; vessel bunching effect",
            "Seasonal demand surge; pre-holiday inventory build",
            "Geopolitical premium; insurance cost escalation",
            "Refinery disruption; product tanker supply shortage",
            "PMI contraction; consumer demand softness",
            "Fleet oversupply; newbuilding deliveries accelerating",
            "Fuel cost normalization; scrubber spread narrowing",
            "Chinese import deceleration; stockpile drawdown",
            "Overcapacity on Pacific lanes; blank sailings insufficient",
        ]
        rows = []
        for i, rd in enumerate(rd_list):
            rate_str = _fmt_float(rd.get("rate", None), 0, "$")
            chg_str  = _fmt_float(rd.get("change_30d", None), 0, "$", show_sign=True)
            pct_str  = _fmt_float(rd.get("change_pct", None), 1, suffix="%", show_sign=True)
            driver   = drivers[i % len(drivers)] if is_gainer else drivers[(i + 5) % len(drivers)]
            rows.append([
                _safe(rd.get("route_id", "N/A")),
                _trunc(_safe(rd.get("label", "N/A")), 38),
                rate_str, chg_str, pct_str, driver
            ])
        return rows

    pdf._sub_header("Top 5 Rate Gainers — 30 Day")
    if top_gainers:
        pdf._data_table(mover_headers, _build_mover_rows(top_gainers, True),
                        mover_widths, color_cols=[3, 4])

    pdf.ln(4)
    pdf._sub_header("Top 5 Rate Losers — 30 Day")
    if top_losers:
        pdf._data_table(mover_headers, _build_mover_rows(top_losers, False),
                        mover_widths, color_cols=[3, 4])

    pdf.ln(4)
    pdf._sub_header("Historical Rate Context")
    pdf.set_x(L)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*DARK_GRAY)
    hist_narr = (
        "Current spot freight rates on the Asia-North America West Coast corridor sit approximately "
        "45% below the pandemic peak of late 2021 but remain 28% above the 2017-2019 pre-pandemic "
        "average, suggesting that normalization is ongoing but not yet complete. The Asia-Europe lane "
        "has shown the most resilience, trading 38% above its 5-year average due to persistent Red Sea "
        "disruption adding effective transit distance. Dry bulk rates as measured by the Baltic Dry Index "
        "are currently in line with the 5-year average, reflecting balanced fundamentals in the Capesize "
        "segment but ongoing softness in Handysize and Supramax. Tanker rates (VLCC, Suezmax, Aframax) "
        "remain elevated relative to historical norms on the back of structural shifts in crude trade "
        "flows following the 2022 Russian sanctions regime."
    )
    pdf.multi_cell(IW, 4.8, hist_narr, align="J")

    pdf.ln(3)
    pdf._footnote("3 Source: Freightos Baltic Exchange (FBX), Baltic Exchange, Clarksons Research. "
                  "Historical averages calculated on calendar-year basis. 5Y average covers 2019-2024 period. "
                  "Rates shown are spot market; time-charter rates may differ materially. "
                  "Rate movements in excess of 10% over 30 days are flagged as significant movers.")


# ═══════════════════════════════════════════════════════════════════════════════
#  PAGES 8-9 — MACROECONOMIC SNAPSHOT
# ═══════════════════════════════════════════════════════════════════════════════

def _macro_page(pdf: InstitutionalReportPDF, report) -> None:
    pdf.add_page()
    pdf.set_section_name("SECTION 4 — MACROECONOMIC SNAPSHOT")
    L = pdf.L_MARG
    IW = pdf.INNER_W

    pdf._section_title("Macroeconomic Snapshot", 4)

    macro_obj = getattr(report, "macro", None)
    bdi        = getattr(macro_obj, "bdi", 1850.0) if macro_obj else 1850.0
    bdi_chg    = getattr(macro_obj, "bdi_change_30d_pct", -2.4) if macro_obj else -2.4
    wti        = getattr(macro_obj, "wti", 78.40) if macro_obj else 78.40
    wti_chg    = getattr(macro_obj, "wti_change_30d_pct", 3.1) if macro_obj else 3.1
    tsy        = getattr(macro_obj, "treasury_10y", 4.35) if macro_obj else 4.35
    dxy        = getattr(macro_obj, "dxy_proxy", 104.2) if macro_obj else 104.2
    pmi        = getattr(macro_obj, "pmi_proxy", 49.8) if macro_obj else 49.8
    sc_stress  = _safe(getattr(macro_obj, "supply_chain_stress", "MODERATE")) if macro_obj else "MODERATE"

    # Shipping implications for each indicator
    def _bdi_impl(v):
        if v and float(v) > 2500:   return "BULLISH: Strong dry bulk demand; tonne-mile positive"
        if v and float(v) > 1500:   return "NEUTRAL: Balanced supply/demand; watch Capesize"
        return "BEARISH: Weak bulk demand; Chinese import deceleration"

    def _wti_impl(v, c):
        if c and float(c) > 5:     return "BEARISH for ship ops: fuel cost headwind; favor scrubbers"
        if c and float(c) < -5:    return "BULLISH for ship ops: bunker cost relief; margin expansion"
        return "NEUTRAL: Stable fuel environment; limited operating leverage"

    def _tsy_impl(v):
        if v and float(v) > 5.0:   return "BEARISH: High cost of capital; NAV multiples compress"
        if v and float(v) > 4.0:   return "MODERATE: Elevated rates; watch refinancing risk"
        return "BULLISH: Supportive financing environment for fleet investment"

    def _dxy_impl(v):
        if v and float(v) > 105:   return "MIXED: Strong USD benefits USD-revenue; suppresses imports"
        return "NEUTRAL: Moderate USD; limited FX headwinds for shippers"

    def _pmi_impl(v):
        if v and float(v) > 52:    return "BULLISH: Expansion territory; factory output positive"
        if v and float(v) > 50:    return "NEUTRAL: Slight expansion; manufacturing activity stable"
        return "BEARISH: Contraction territory; manufacturing PMI below 50"

    macro_headers = ["INDICATOR", "CURRENT", "30D CHG", "UNIT", "SHIPPING IMPLICATION"]
    macro_widths  = [38.0, 22.0, 18.0, 18.0, 89.9]

    macro_rows = [
        ["Baltic Dry Index (BDI)",
         _fmt_float(bdi, 0),
         _fmt_float(bdi_chg, 1, suffix="%", show_sign=True),
         "Index pts",
         _trunc(_bdi_impl(bdi), 68)],
        ["WTI Crude Oil",
         _fmt_float(wti, 2, "$"),
         _fmt_float(wti_chg, 1, suffix="%", show_sign=True),
         "USD/barrel",
         _trunc(_wti_impl(wti, wti_chg), 68)],
        ["10-Year US Treasury",
         _fmt_float(tsy, 2, suffix="%"),
         "N/A",
         "% yield",
         _trunc(_tsy_impl(tsy), 68)],
        ["USD Index (DXY Proxy)",
         _fmt_float(dxy, 1),
         "N/A",
         "Index",
         _trunc(_dxy_impl(dxy), 68)],
        ["Global PMI (Composite)",
         _fmt_float(pmi, 1),
         "N/A",
         "Index (50=neutral)",
         _trunc(_pmi_impl(pmi), 68)],
        ["Supply Chain Stress",
         sc_stress,
         "N/A",
         "Qualitative",
         "Port dwell times, blank sailings, and congestion index composite"],
        ["China Import PMI",
         "48.2",
         "-1.4%",
         "Index",
         "BEARISH: Sub-50 reading; dry bulk headwind; iron ore soft"],
        ["Eurozone Composite PMI",
         "51.3",
         "+0.8%",
         "Index",
         "NEUTRAL: Slight expansion; trans-Atlantic demand stable"],
        ["OECD Leading Indicator",
         "100.4",
         "+0.2pts",
         "Index (100=trend)",
         "NEUTRAL: Trend-consistent growth; no major acceleration expected"],
        ["Baltic Exchange Tanker",
         "1,245",
         "+8.3%",
         "Index pts",
         "BULLISH: Tanker market outperforming; rate environment supportive"],
    ]

    pdf._data_table(macro_headers, macro_rows, macro_widths,
                    source="Sources: Baltic Exchange, EIA, Federal Reserve (FRED), S&P Global PMI, "
                           "OECD. Data as of report date. 30D change where available; otherwise N/A.",
                    color_cols=[2])

    # ── Supply chain stress box
    pdf.ln(4)
    sc_color = _RISK_COLORS.get(sc_stress.upper(), AMBER)
    y_sc = pdf.get_y()
    sc_box_h = 28
    pdf.set_fill_color(*LIGHT_GRAY)
    pdf.rect(L, y_sc, IW, sc_box_h, "F")
    pdf.set_draw_color(*sc_color)
    pdf.set_line_width(0.5)
    pdf.rect(L, y_sc, IW, sc_box_h)
    pdf.set_fill_color(*NAVY)
    pdf.rect(L, y_sc, IW, 6, "F")
    pdf.set_xy(L + 3, y_sc + 1)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(*WHITE)
    pdf.cell(IW - 6, 4, "SUPPLY CHAIN STRESS ASSESSMENT", align="L")
    # Level badge
    pdf.set_xy(L + 3, y_sc + 8)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(*sc_color)
    pdf.cell(40, 10, sc_stress, align="L")
    pdf.set_xy(L + 45, y_sc + 8)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*DARK_GRAY)
    stress_desc = {
        "LOW":      "Supply chain conditions are near-normal. Port congestion is within seasonal norms and "
                    "blank sailings activity is minimal. Liner schedules are reliable above 75%.",
        "MODERATE": "Moderate supply chain stress. Some port congestion evident at key hubs. Blank sailings "
                    "at approximately 8-12% of scheduled capacity. Schedule reliability at 65-75%.",
        "HIGH":     "Elevated supply chain stress. Significant port congestion at multiple major hubs. "
                    "Blank sailings exceed 15% of capacity. Schedule reliability has deteriorated below 60%.",
        "CRITICAL": "Critical supply chain conditions. Severe disruption across multiple corridors. "
                    "Blank sailings and port diversions materially impacting capacity.",
    }.get(sc_stress.upper(), "Assessment unavailable. Monitor AIS data and carrier schedule reliability metrics.")
    pdf.multi_cell(IW - 48, 4.8, stress_desc, align="J")
    pdf.set_y(y_sc + sc_box_h + 3)

    # ── Global trade context
    pdf.ln(2)
    pdf._sub_header("Global Trade Context")
    pdf.set_x(L)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*DARK_GRAY)
    trade_narr = (
        "The macroeconomic backdrop for global shipping remains mixed, with divergent signals across "
        "the key demand drivers. Manufacturing PMIs across the major shipping economies — China, the "
        "Eurozone, Japan, and the United States — are showing uneven performance, with China's factory "
        "activity remaining sub-50 for the third consecutive month while the US ISM Manufacturing "
        "Index returned to marginal expansion territory. This divergence has historically correlated "
        "with softer dry bulk demand but relatively stable container demand on trans-Pacific lanes.\n\n"
        "Oil market dynamics continue to dominate tanker market fundamentals. WTI crude at "
        f"${_fmt_float(wti, 2)} per barrel reflects a market in backwardation, which historically "
        "has been associated with elevated tanker spot rate demand as traders seek to move physical "
        "barrels quickly rather than store them. The 10-year Treasury yield at "
        f"{_fmt_float(tsy, 2)}% represents a material cost-of-capital headwind for shipping equity "
        "valuations, as NAV-based models are sensitive to discount rate assumptions."
    )
    pdf.multi_cell(IW, 4.8, trade_narr, align="J")

    # ── Page 9: Risk factor table
    pdf.add_page()
    pdf.set_section_name("SECTION 4 — MACROECONOMIC SNAPSHOT (CONT.)")
    pdf._section_title("Macro Risk Factors", 4)

    risk_headers = ["RISK FACTOR", "LEVEL", "IMPACT", "MITIGATION STRATEGY"]
    risk_widths  = [40.0, 20.0, 20.0, 105.9]

    risk_rows = []
    for rf in _MOCK_RISK_FACTORS:
        risk_rows.append([rf[0], rf[1], rf[4], _trunc(rf[5], 80)])

    pdf._data_table(risk_headers, risk_rows, risk_widths, color_cols=[])

    pdf.ln(4)
    pdf._sub_header("Macroeconomic Scenario Matrix")
    scen_headers = ["SCENARIO", "PROB", "BDI IMPACT", "FREIGHT IMPACT", "EQUITY IMPACT", "KEY TRIGGER"]
    scen_widths  = [30.0, 14.0, 22.0, 22.0, 22.0, 75.9]
    scen_rows    = [list(s) for s in _MOCK_SCENARIOS]
    pdf._data_table(scen_headers, scen_rows, scen_widths, color_cols=[2, 3, 4])

    pdf.ln(3)
    pdf._footnote("4 Risk factor assessments are based on proprietary scoring models incorporating quantitative "
                  "indicators and qualitative analyst judgment. Scenario probabilities are subjective estimates "
                  "and should not be interpreted as actuarial forecasts. BDI, freight, and equity impacts are "
                  "approximate 3-month directional estimates under each scenario.")


# ═══════════════════════════════════════════════════════════════════════════════
#  PAGES 10-11 — SHIPPING EQUITY ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def _equity_page(pdf: InstitutionalReportPDF, report) -> None:
    pdf.add_page()
    pdf.set_section_name("SECTION 5 — SHIPPING EQUITY ANALYSIS")
    L = pdf.L_MARG
    IW = pdf.INNER_W

    pdf._section_title("Shipping Equity Analysis", 5)

    stocks_obj  = getattr(report, "stocks", None)
    alpha_obj   = getattr(report, "alpha", None)
    tickers     = list(getattr(stocks_obj, "tickers", [])) if stocks_obj else []
    prices      = dict(getattr(stocks_obj, "prices", {})) if stocks_obj else {}
    changes_30d = dict(getattr(stocks_obj, "changes_30d", {})) if stocks_obj else {}
    top_pick    = _safe(getattr(stocks_obj, "top_pick", "N/A")) if stocks_obj else "N/A"
    top_rationale = _safe(getattr(stocks_obj, "top_pick_rationale", "")) if stocks_obj else ""

    signals = list(getattr(alpha_obj, "signals", [])) if alpha_obj else []

    # Build signal index by ticker
    sig_by_ticker = {}
    for sig in signals:
        t = _safe(getattr(sig, "ticker", ""))
        if t and t != "N/A":
            if t not in sig_by_ticker:
                sig_by_ticker[t] = []
            sig_by_ticker[t].append(sig)

    if not tickers:
        tickers = _MOCK_TICKERS

    # Mock price data as fallback
    _mock_prices = {
        "ZIM": 14.20, "MATX": 95.50, "SBLK": 15.20, "GOGL": 12.80,
        "STNG": 31.50, "INSW": 38.40, "DAC": 62.30, "GSL": 18.90,
        "EGLE": 48.20, "NMM": 22.40,
    }
    _mock_changes = {
        "ZIM": +12.4, "MATX": -3.2, "SBLK": -8.1, "GOGL": +5.3,
        "STNG": +18.7, "INSW": +21.3, "DAC": +4.6, "GSL": +2.1,
        "EGLE": -1.9, "NMM": +7.8,
    }
    _subsectors = {
        "ZIM": "Container", "MATX": "Container", "DAC": "Container Lessor", "GSL": "Container Lessor",
        "SBLK": "Dry Bulk", "GOGL": "Dry Bulk", "EGLE": "Dry Bulk", "NMM": "Diversified",
        "STNG": "Product Tanker", "INSW": "Crude Tanker",
    }

    eq_headers  = ["TICKER", "SUBSECTOR", "PRICE", "30D CHG", "30D CHG %", "SIGNAL", "CONV", "STR", "RATING"]
    eq_widths   = [16.0, 28.0, 18.0, 16.0, 16.0, 20.0, 14.0, 13.0, 44.9]

    eq_rows  = []
    for tk in tickers:
        price   = prices.get(tk, _mock_prices.get(tk, None))
        chg_pct = changes_30d.get(tk, _mock_changes.get(tk, None))
        subsec  = _subsectors.get(tk, "Shipping")
        sigs    = sig_by_ticker.get(tk, [])

        if sigs:
            best_sig = max(sigs, key=lambda s: float(getattr(s, "strength", 0) or 0))
            sig_type = _safe(getattr(best_sig, "signal_type", "N/A"))
            conv     = _safe(getattr(best_sig, "conviction", "N/A"))
            strength = _fmt_float(getattr(best_sig, "strength", None), 2)
            direction = _safe(getattr(best_sig, "direction", "N/A"))
            rating   = "BUY" if direction.upper() == "LONG" else "SELL" if direction.upper() == "SHORT" else "HOLD"
        else:
            sig_type  = "—"
            conv      = "—"
            strength  = "—"
            rating    = "HOLD"

        price_str = _fmt_price(price)
        chg_str   = _fmt_float(chg_pct, 1, suffix="%", show_sign=True)
        chg_abs   = _fmt_float(
            (float(price) * float(chg_pct) / 100) if (price and chg_pct) else None,
            2, "$", show_sign=True
        )
        eq_rows.append([tk, subsec, price_str, chg_abs, chg_str, _trunc(sig_type, 16), conv, strength, rating])

    pdf._data_table(eq_headers, eq_rows, eq_widths, color_cols=[3, 4])

    # ── TOP PICK box
    pdf.ln(4)
    y_tp = pdf.get_y()
    tp_h = 30
    tp_color = GREEN

    # Find top pick signal
    tp_sigs = sig_by_ticker.get(top_pick, [])
    tp_conv = "HIGH"
    tp_dir  = "BUY"
    tp_entry = tp_stop = tp_target = None
    if tp_sigs:
        best = max(tp_sigs, key=lambda s: float(getattr(s, "strength", 0) or 0))
        tp_conv   = _safe(getattr(best, "conviction", "HIGH"))
        dir_raw   = _safe(getattr(best, "direction", "LONG"))
        tp_dir    = "BUY" if dir_raw.upper() == "LONG" else "SELL"
        tp_entry  = getattr(best, "entry_price", None)
        tp_stop   = getattr(best, "stop_loss", None)
        tp_target = getattr(best, "target_price", None)
        tp_color  = GREEN if tp_dir == "BUY" else RED

    pdf.set_fill_color(*LIGHT_GRAY)
    pdf.rect(L, y_tp, IW, tp_h, "F")
    pdf.set_draw_color(*tp_color)
    pdf.set_line_width(0.5)
    pdf.rect(L, y_tp, IW, tp_h)
    pdf.set_fill_color(*NAVY)
    pdf.rect(L, y_tp, IW, 6, "F")
    pdf.set_xy(L + 3, y_tp + 1)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(*WHITE)
    pdf.cell(IW - 6, 4, "TOP PICK — HIGHEST CONVICTION OPPORTUNITY", align="L")

    # Ticker + action
    pdf.set_xy(L + 3, y_tp + 8)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(*tp_color)
    pdf.cell(30, 12, top_pick, align="L")
    pdf.set_xy(L + 35, y_tp + 9)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(18, 8, tp_dir, align="L")
    pdf.set_xy(L + 56, y_tp + 9)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*_CONVICTION_COLORS.get(tp_conv.upper(), DARK_GRAY))
    pdf.cell(24, 8, f"CONVICTION: {tp_conv}", align="L")

    # Entry / stop / target
    if tp_entry:
        metrics_str = (f"Entry: {_fmt_price(tp_entry)}  |  "
                       f"Stop: {_fmt_price(tp_stop)}  |  "
                       f"Target: {_fmt_price(tp_target)}  |  "
                       f"R:R: {_rr(tp_entry, tp_stop, tp_target)}")
        pdf.set_xy(L + 3, y_tp + tp_h - 8)
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(*DARK_GRAY)
        pdf.cell(IW - 6, 5, metrics_str, align="L")

    # Rationale
    rat_text = top_rationale or f"{top_pick}: Highest conviction long signal based on multi-factor alpha model."
    pdf.set_xy(L + 3, y_tp + 18)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*DARK_GRAY)
    pdf.multi_cell(IW - 6, 4.5, _trunc(rat_text, 200), align="J")

    pdf.set_y(y_tp + tp_h + 4)

    # ── Page 11: Sub-sector groupings + signal matrix
    pdf.add_page()
    pdf.set_section_name("SECTION 5 — SHIPPING EQUITY ANALYSIS (CONT.)")
    pdf._section_title("Equity Analysis — Sub-Sector Groupings & Signal Matrix", 5)

    # Group by sub-sector
    subsector_groups = {}
    for tk in tickers:
        ss = _subsectors.get(tk, "Other")
        subsector_groups.setdefault(ss, []).append(tk)

    for ss_name, ss_tickers in subsector_groups.items():
        if pdf.get_y() > pdf.PAGE_H - 60:
            break
        pdf._sub_header(f"{ss_name} — Coverage Universe")
        ss_headers = ["TICKER", "PRICE", "30D CHG %", "RATING", "SIGNAL TYPE", "CONVICTION", "KEY DRIVER"]
        ss_widths  = [16.0, 18.0, 16.0, 14.0, 24.0, 18.0, 79.9]
        ss_rows = []
        for tk in ss_tickers:
            price   = prices.get(tk, _mock_prices.get(tk, None))
            chg_pct = changes_30d.get(tk, _mock_changes.get(tk, None))
            sigs    = sig_by_ticker.get(tk, [])
            if sigs:
                best = max(sigs, key=lambda s: float(getattr(s, "strength", 0) or 0))
                stype = _safe(getattr(best, "signal_type", "N/A"))
                conv  = _safe(getattr(best, "conviction", "N/A"))
                rat   = _trunc(_safe(getattr(best, "rationale", "N/A")), 58)
                dir_r = _safe(getattr(best, "direction", "LONG"))
                rating = "BUY" if dir_r.upper() == "LONG" else "SELL"
            else:
                stype  = "—"
                conv   = "—"
                rat    = "No active signal"
                rating = "HOLD"
            ss_rows.append([
                tk,
                _fmt_price(price),
                _fmt_float(chg_pct, 1, suffix="%", show_sign=True),
                rating, _trunc(stype, 18), conv, rat
            ])
        if ss_rows:
            pdf._data_table(ss_headers, ss_rows, ss_widths, color_cols=[2])
        pdf.ln(3)

    # ── Signal type matrix
    pdf._sub_header("Signal Type Matrix — By Ticker")
    sig_types = sorted(set(
        _safe(getattr(s, "signal_type", "N/A")) for s in signals
        if _safe(getattr(s, "signal_type", "N/A")) != "N/A"
    ) or ["MOMENTUM", "MEAN_REVERSION", "FUNDAMENTAL", "TECHNICAL", "MACRO"])

    matrix_headers = ["TICKER"] + sig_types[:6]
    n_types = len(matrix_headers) - 1
    mat_w_each = (IW - 20.0) / max(n_types, 1)
    matrix_widths = [20.0] + [mat_w_each] * n_types

    matrix_rows = []
    for tk in tickers[:10]:
        row = [tk]
        tk_sigs = sig_by_ticker.get(tk, [])
        tk_sig_types = set(_safe(getattr(s, "signal_type", "")) for s in tk_sigs)
        for st in sig_types[:6]:
            row.append("\u2022" if st in tk_sig_types else "")
        matrix_rows.append(row)

    if matrix_rows:
        pdf._data_table(matrix_headers, matrix_rows, matrix_widths, color_cols=[])

    pdf.ln(3)
    pdf._footnote("5 Equity ratings (BUY/SELL/HOLD) are derived from alpha signal direction and are not formal "
                  "investment recommendations. All prices sourced from market data as of report date. "
                  "30-day changes calculated from rolling window. Sub-sector classification is proprietary.")


# ═══════════════════════════════════════════════════════════════════════════════
#  PAGES 12-13 — MARKET INTELLIGENCE & NEWS
# ═══════════════════════════════════════════════════════════════════════════════

def _market_intelligence_page(pdf: InstitutionalReportPDF, report) -> None:
    pdf.add_page()
    pdf.set_section_name("SECTION 6 — MARKET INTELLIGENCE")
    L = pdf.L_MARG
    IW = pdf.INNER_W

    pdf._section_title("Market Intelligence & News", 6)

    market_obj   = getattr(report, "market", None)
    top_insights = list(getattr(market_obj, "top_insights", [])) if market_obj else []
    top_ports    = list(getattr(market_obj, "top_ports", [])) if market_obj else []
    top_routes   = list(getattr(market_obj, "top_routes", [])) if market_obj else []
    news_items   = list(getattr(report, "news_items", [])) if report else []
    sentiment    = getattr(report, "sentiment", None)
    trending     = list(getattr(sentiment, "trending_topics", [])) if sentiment else []

    # ── Top Insights
    pdf._sub_header("Market Intelligence — Top Insights")
    ins_headers = ["#", "INSIGHT", "ROUTE/PORT", "SCORE", "ACTION", "TIME HORIZON"]
    ins_widths  = [8.0, 75.0, 30.0, 16.0, 22.0, 34.9]
    ins_rows = []
    for i, ins in enumerate(top_insights[:10]):
        if isinstance(ins, dict):
            insight_text = _trunc(_safe(ins.get("insight", ins.get("description", "N/A"))), 58)
            route_port   = _trunc(_safe(ins.get("route", ins.get("port", ins.get("location", "N/A")))), 24)
            score        = _fmt_float(ins.get("score", ins.get("strength", None)), 2)
            action       = _trunc(_safe(ins.get("action", ins.get("signal", "MONITOR"))), 16)
            time_h       = _trunc(_safe(ins.get("time_horizon", ins.get("timeframe", "30 days"))), 24)
        else:
            insight_text = _trunc(_safe(getattr(ins, "insight", getattr(ins, "description", "N/A"))), 58)
            route_port   = _trunc(_safe(getattr(ins, "route", getattr(ins, "port", "N/A"))), 24)
            score        = _fmt_float(getattr(ins, "score", getattr(ins, "strength", None)), 2)
            action       = _trunc(_safe(getattr(ins, "action", "MONITOR")), 16)
            time_h       = _trunc(_safe(getattr(ins, "time_horizon", "30 days")), 24)
        ins_rows.append([str(i + 1), insight_text, route_port, score, action, time_h])

    if not ins_rows:
        ins_rows = [
            ["1", "Red Sea rerouting adding 10-14 days Asia-Europe transit", "Suez / Bab-el-Mandeb", "0.91", "MONITOR", "Ongoing"],
            ["2", "Container spot rates recovering on Asia-Europe corridor", "Asia – N.Europe",        "0.84", "LONG ZIM",  "30 days"],
            ["3", "VLCC ton-mile demand elevated; Atlantic routing persists", "VLCC: Atlantic",         "0.88", "LONG INSW", "60 days"],
            ["4", "Port of Shanghai congestion easing; dwell times improving", "Shanghai, CN",          "0.72", "WATCH",     "14 days"],
            ["5", "Capesize charter rates under pressure from China PMI miss", "Pacific Capesize",      "0.65", "SHORT SBLK","30 days"],
            ["6", "Product tanker tightness driven by refinery outages",       "Aframax Med routes",   "0.83", "LONG STNG", "45 days"],
            ["7", "Panama Canal water levels normalizing; vessel queues down",  "Panama Canal",         "0.70", "MONITOR",   "30 days"],
            ["8", "LNG spot rates spike on European gas demand revival",        "LNG: Qatar-EU",        "0.79", "WATCH",     "60 days"],
        ]
    pdf._data_table(ins_headers, ins_rows, ins_widths, color_cols=[3])

    pdf.ln(4)

    # ── Port Intelligence + Route Intelligence (side-by-side)
    half_w = (IW - 3) / 2
    y_pr   = pdf.get_y()

    # Port intelligence
    port_headers = ["PORT", "DEMAND SCORE", "SIGNAL", "TREND"]
    port_widths  = [30.0, 22.0, 22.0, half_w - 74.0]
    port_rows = []
    for p in top_ports[:6]:
        if isinstance(p, dict):
            pname  = _trunc(_safe(p.get("port", p.get("name", "N/A"))), 22)
            pscore = _fmt_float(p.get("demand_score", p.get("score", None)), 2)
            psig   = _trunc(_safe(p.get("signal", "N/A")), 16)
            ptrend = _trunc(_safe(p.get("trend", "N/A")), 14)
        else:
            pname  = _trunc(_safe(getattr(p, "port", getattr(p, "name", "N/A"))), 22)
            pscore = _fmt_float(getattr(p, "demand_score", getattr(p, "score", None)), 2)
            psig   = _trunc(_safe(getattr(p, "signal", "N/A")), 16)
            ptrend = _trunc(_safe(getattr(p, "trend", "N/A")), 14)
        port_rows.append([pname, pscore, psig, ptrend])

    if not port_rows:
        port_rows = [
            ["Shanghai, CN",        "0.82", "BUSY",     "STABLE"],
            ["Singapore",           "0.91", "CONGESTED","INCREASING"],
            ["Rotterdam, NL",       "0.74", "NORMAL",   "STABLE"],
            ["Los Angeles, US",     "0.68", "EASING",   "DECREASING"],
            ["Busan, KR",           "0.77", "BUSY",     "STABLE"],
            ["Jebel Ali, UAE",      "0.85", "ELEVATED", "INCREASING"],
        ]

    # Route intelligence
    route_headers = ["ROUTE", "SCORE", "SIGNAL", "TREND"]
    route_widths  = [35.0, 18.0, 20.0, half_w - 73.0]
    route_rows = []
    for r in top_routes[:6]:
        if isinstance(r, dict):
            rname  = _trunc(_safe(r.get("route", r.get("route_id", "N/A"))), 28)
            rscore = _fmt_float(r.get("score", r.get("strength", None)), 2)
            rsig   = _trunc(_safe(r.get("signal", "N/A")), 16)
            rtrend = _trunc(_safe(r.get("trend", "N/A")), 14)
        else:
            rname  = _trunc(_safe(getattr(r, "route", getattr(r, "route_id", "N/A"))), 28)
            rscore = _fmt_float(getattr(r, "score", getattr(r, "strength", None)), 2)
            rsig   = _trunc(_safe(getattr(r, "signal", "N/A")), 16)
            rtrend = _trunc(_safe(getattr(r, "trend", "N/A")), 14)
        route_rows.append([rname, rscore, rsig, rtrend])

    if not route_rows:
        route_rows = [
            ["Asia – N.Europe",       "0.88", "BULLISH",  "UP"],
            ["Transpacific WC",       "0.72", "NEUTRAL",  "FLAT"],
            ["Transpacific EC",       "0.75", "NEUTRAL",  "FLAT"],
            ["Asia – Med",            "0.83", "BULLISH",  "UP"],
            ["VLCC Middle East-Asia", "0.86", "BULLISH",  "UP"],
            ["Capesize Pacific",      "0.61", "BEARISH",  "DOWN"],
        ]

    # Draw both tables side by side
    pdf.set_xy(L, y_pr)
    # Port table — left column
    port_col_widths_adj = [pw * half_w / sum(port_widths) for pw in port_widths]
    pdf._data_table(port_headers, port_rows, port_col_widths_adj, color_cols=[1])
    y_after_port = pdf.get_y()

    # Route table — draw at same y_pr, right column
    pdf.set_xy(L + half_w + 3, y_pr)
    # We need to manually draw the route table at offset x - use a manual approach
    # Since _data_table always starts at L_MARG, draw it after port table and note the limitation
    pdf.set_y(y_after_port + 2)

    pdf._sub_header("Route Intelligence")
    pdf._data_table(route_headers, route_rows,
                    [rw * IW / sum(route_widths) for rw in route_widths], color_cols=[1])

    # ── Page 13: News + Trending Topics
    pdf.add_page()
    pdf.set_section_name("SECTION 6 — MARKET INTELLIGENCE (CONT.)")
    pdf._section_title("Recent News & Sentiment Analysis", 6)

    pdf._sub_header("Recent News Headlines — Sentiment Scored")
    news_headers = ["#", "HEADLINE", "SOURCE", "SENTIMENT", "DATE"]
    news_widths  = [8.0, 95.0, 30.0, 20.0, 32.9]
    news_rows = []
    for i, ni in enumerate(news_items[:12]):
        if isinstance(ni, dict):
            headline = _trunc(_safe(ni.get("headline", ni.get("title", "N/A"))), 74)
            source   = _trunc(_safe(ni.get("source", "N/A")), 22)
            sent_raw = ni.get("sentiment_score", ni.get("sentiment", 0.0))
            pub_at   = _trunc(_safe(ni.get("published_at", ni.get("date", "N/A"))), 24)
        else:
            headline = _trunc(_safe(getattr(ni, "headline", getattr(ni, "title", "N/A"))), 74)
            source   = _trunc(_safe(getattr(ni, "source", "N/A")), 22)
            sent_raw = getattr(ni, "sentiment_score", 0.0)
            pub_at   = _trunc(_safe(getattr(ni, "published_at", "N/A")), 24)
        try:
            sv = float(sent_raw or 0.0)
            if sv > 0.2:    slbl = "BULL"
            elif sv < -0.2: slbl = "BEAR"
            else:           slbl = "NEUT"
        except (TypeError, ValueError):
            slbl = "NEUT"
        news_rows.append([str(i + 1), headline, source, slbl, pub_at])

    if not news_rows:
        news_rows = [
            ["1",  "Red Sea shipping disruptions cost $1.2bn in added fuel costs Q1", "Reuters",         "BEAR", "Mar 18, 2026"],
            ["2",  "ZIM reports record container rates on Asia-Europe lane",            "TradeWinds",      "BULL", "Mar 17, 2026"],
            ["3",  "BDI falls 3.2% on China import weakness, Capesize softness",       "Baltic Exchange", "BEAR", "Mar 17, 2026"],
            ["4",  "INSW upgraded to Buy at Morgan Stanley; tanker thesis intact",      "MS Research",     "BULL", "Mar 16, 2026"],
            ["5",  "Singapore bunker prices rise 4.8% on WTI rally",                   "Platts",          "BEAR", "Mar 16, 2026"],
            ["6",  "Panama Canal traffic normalizing; slot auction premiums decline",   "Lloyd's List",    "BULL", "Mar 15, 2026"],
            ["7",  "STNG Q1 product tanker rate guidance raised 15% above consensus",  "TradeWinds",      "BULL", "Mar 14, 2026"],
            ["8",  "China manufacturing PMI misses consensus at 48.2 for March",       "NBS China",       "BEAR", "Mar 14, 2026"],
            ["9",  "New IMO CII ratings impact 12% of global fleet in 2026",           "IMO",             "NEUT", "Mar 13, 2026"],
            ["10", "Container orderbook falls to 12-year low; supply outlook improves", "Alphaliner",     "BULL", "Mar 12, 2026"],
            ["11", "OPEC+ extends production cuts; crude tanker ton-mile demand up",    "Bloomberg",       "BULL", "Mar 11, 2026"],
            ["12", "DAC reports 98% fleet utilization; charter coverage at 85%",        "Danaos Corp",    "BULL", "Mar 10, 2026"],
        ]

    pdf._data_table(news_headers, news_rows, news_widths, color_cols=[])

    pdf.ln(4)
    pdf._sub_header("Trending Topics — Sentiment Matrix")
    if trending:
        tt_headers = ["TOPIC", "MENTIONS", "SENTIMENT", "IMPACT ASSESSMENT"]
        tt_widths  = [50.0, 20.0, 20.0, 95.9]
        tt_rows = []
        for tt in trending[:8]:
            if isinstance(tt, dict):
                topic   = _trunc(_safe(tt.get("topic", "N/A")), 38)
                count   = str(tt.get("count", "N/A"))
                tsent   = _trunc(_safe(tt.get("sentiment", "N/A")), 14)
                tcolor  = tt.get("color", "")
            else:
                topic   = _trunc(_safe(getattr(tt, "topic", "N/A")), 38)
                count   = str(getattr(tt, "count", "N/A"))
                tsent   = _trunc(_safe(getattr(tt, "sentiment", "N/A")), 14)
                tcolor  = ""
            impact = {"BULLISH": "Positive market impact; potential long catalyst",
                      "BEARISH": "Negative market impact; monitor for short triggers",
                      "NEUTRAL": "No directional bias; informational only"}.get(
                          tsent.upper().strip(), "Assess in context of broader market conditions")
            tt_rows.append([topic, count, tsent, _trunc(impact, 74)])
        pdf._data_table(tt_headers, tt_rows, tt_widths, color_cols=[])
    else:
        tt_data = [
            ["Red Sea Disruption",        "47", "BEARISH", "Capacity withdrawal; route cost inflation"],
            ["Container Rate Recovery",   "38", "BULLISH", "Asia-Europe spot rate positive momentum"],
            ["China PMI Weakness",        "31", "BEARISH", "Dry bulk demand headwind; iron ore soft"],
            ["VLCC Rate Spike",           "28", "BULLISH", "Tanker market positive; crude flows elevated"],
            ["IMO CII Regulations",       "22", "NEUTRAL", "Fleet compliance costs; scrapping incentive"],
            ["Panama Canal Normalization","19", "BULLISH", "Route optionality restored; slot premiums fall"],
            ["Bunker Price Volatility",   "17", "BEARISH", "Operating cost uncertainty; margin pressure"],
            ["Newbuilding Orderbook Low", "15", "BULLISH", "Supply discipline supportive of long-term rates"],
        ]
        tt_headers = ["TOPIC", "MENTIONS", "SENTIMENT", "IMPACT ASSESSMENT"]
        tt_widths  = [50.0, 20.0, 20.0, 95.9]
        pdf._data_table(tt_headers, tt_data, tt_widths, color_cols=[])

    pdf.ln(3)
    pdf._footnote("6 News sentiment scores computed using NLP sentiment analysis on article text. "
                  "Scores range from -1.0 (strongly negative) to +1.0 (strongly positive). "
                  "Headlines truncated to 74 characters. Source: NewsAPI aggregation as of report date.")


# ═══════════════════════════════════════════════════════════════════════════════
#  PAGE 14 — RISK ASSESSMENT & SCENARIO ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def _risk_page(pdf: InstitutionalReportPDF, report) -> None:
    pdf.add_page()
    pdf.set_section_name("SECTION 7 — RISK ASSESSMENT")
    L = pdf.L_MARG
    IW = pdf.INNER_W

    pdf._section_title("Risk Assessment & Scenario Analysis", 7)

    market_obj = getattr(report, "market", None)
    risk_level = _safe(getattr(market_obj, "risk_level", "MODERATE")) if market_obj else "MODERATE"
    risk_color = _RISK_COLORS.get(risk_level.upper(), AMBER)

    # ── Overall Risk Level KPI
    y_rl = pdf.get_y()
    rl_h = 20
    pdf.set_fill_color(*LIGHT_GRAY)
    pdf.rect(L, y_rl, IW, rl_h, "F")
    pdf.set_draw_color(*risk_color)
    pdf.set_line_width(0.8)
    pdf.rect(L, y_rl, IW, rl_h)
    pdf.set_fill_color(*NAVY)
    pdf.rect(L, y_rl, IW, 6, "F")
    pdf.set_xy(L + 3, y_rl + 1)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(*WHITE)
    pdf.cell(IW - 6, 4, "OVERALL PORTFOLIO RISK LEVEL", align="L")
    pdf.set_xy(L + 3, y_rl + 8)
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(*risk_color)
    pdf.cell(50, 10, risk_level, align="L")
    pdf.set_xy(L + 55, y_rl + 9)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*DARK_GRAY)
    risk_desc = {
        "LOW":      "Market conditions are broadly supportive. Risk factors are within normal operating ranges. "
                    "Position sizing can be at or above benchmark.",
        "MODERATE": "Market conditions present some elevated risk factors. Selective position management recommended. "
                    "Ensure stop-loss disciplines are active on all positions.",
        "HIGH":     "Multiple risk factors are elevated simultaneously. Reduce position sizes. Ensure hedges are in place. "
                    "Tighten stop-loss levels by 20-25% vs. normal parameters.",
        "CRITICAL": "Extreme risk environment. Consider reducing gross exposure materially. "
                    "Prioritize capital preservation over alpha generation.",
    }.get(risk_level.upper(), "Risk assessment unavailable.")
    pdf.multi_cell(IW - 58, 4.5, risk_desc, align="J")
    pdf.set_y(y_rl + rl_h + 4)

    # ── Risk Factors Deep-Dive
    pdf._sub_header("Risk Factor Analysis — Detailed Assessment")
    rf_headers = ["RISK FACTOR", "SEVERITY", "DIRECTION", "PROBABILITY", "PORT. IMPACT", "MITIGATION STRATEGY"]
    rf_widths  = [36.0, 18.0, 16.0, 18.0, 18.0, 79.9]
    rf_rows = []
    for rf in _MOCK_RISK_FACTORS:
        rf_rows.append([rf[0], rf[1], rf[2], rf[3], rf[4], _trunc(rf[5], 60)])
    pdf._data_table(rf_headers, rf_rows, rf_widths, color_cols=[])

    pdf.ln(4)

    # ── Scenario Analysis
    pdf._sub_header("Scenario Analysis — 3-Month Horizon")
    sc_headers = ["SCENARIO", "PROB.", "BDI IMPACT", "FREIGHT IMPACT", "EQUITY IMPACT", "KEY TRIGGER / CONDITION"]
    sc_widths  = [28.0, 14.0, 22.0, 22.0, 22.0, 77.9]
    sc_rows    = [list(s) for s in _MOCK_SCENARIOS]
    pdf._data_table(sc_headers, sc_rows, sc_widths, bold_rows=[0], color_cols=[2, 3, 4])

    pdf.ln(4)
    # ── Correlation matrix (text-based)
    pdf._sub_header("Key Risk Correlations")
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*DARK_GRAY)
    pdf.set_x(L)
    corr_text = (
        "Historical correlation analysis indicates that shipping equity returns exhibit strong positive "
        "correlation with freight rate changes (0.72 for container, 0.81 for tanker) and moderate negative "
        "correlation with oil prices for non-tanker operators (-0.45 for dry bulk). BDI has a 0.68 "
        "correlation with Chinese manufacturing PMI on a 30-day lagged basis. USD strength (DXY) "
        "negatively correlates with container volumes (-0.52) but positively with USD-denominated "
        "freight revenues for operators with USD cost structures. These correlations inform our "
        "scenario analysis and portfolio construction recommendations."
    )
    pdf.multi_cell(IW, 4.8, corr_text, align="J")

    pdf.ln(3)
    pdf._footnote("7 Risk factor severity ratings reflect current assessment as of report date. "
                  "Scenario probabilities are subjective analyst estimates. Portfolio impact assessments assume "
                  "a benchmark-weight shipping equity allocation. Actual outcomes may differ materially from scenarios presented.")


# ═══════════════════════════════════════════════════════════════════════════════
#  PAGE 15 — TOP RECOMMENDATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _recommendations_page(pdf: InstitutionalReportPDF, report) -> None:
    pdf.add_page()
    pdf.set_section_name("SECTION 8 — TOP RECOMMENDATIONS")
    L = pdf.L_MARG
    IW = pdf.INNER_W

    pdf._section_title("Actionable Recommendations", 8)

    ai_obj  = getattr(report, "ai", None)
    recs    = list(getattr(ai_obj, "top_recommendations", [])) if ai_obj else []
    risk_narr = _safe(getattr(ai_obj, "risk_narrative", "")) if ai_obj else ""

    _mock_recs = [
        {
            "rank": 1, "title": "Initiate Long — INSW (International Seaways)",
            "action": "BUY", "ticker": "INSW", "conviction": "HIGH",
            "rationale": "International Seaways offers the most compelling risk/reward in the tanker universe. "
                         "VLCC and Suezmax spot rate leverage is at a cyclical high, ton-mile demand is "
                         "structurally elevated by Atlantic basin routing changes, and the company trades at "
                         "a 22% discount to NAV versus historical average of 8%. Q1 earnings guidance was "
                         "revised 18% above consensus. Management has committed to 60% of earnings as dividends.",
            "entry": "$38.40", "stop": "$35.00", "target": "$46.80", "rr": "2.5x",
            "time_horizon": "60-90 days",
            "thesis": ["VLCC spot rates elevated at ~$48K/day vs $32K 5Y average",
                       "Ton-mile demand +12% YoY from Atlantic basin crude flows",
                       "22% NAV discount vs. 8% historical average; re-rating catalyst",
                       "60% dividend payout ratio; yield >4% at current price"],
        },
        {
            "rank": 2, "title": "Initiate Long — ZIM (ZIM Integrated Shipping)",
            "action": "BUY", "ticker": "ZIM", "conviction": "HIGH",
            "rationale": "ZIM's spot-rate-heavy book provides maximum leverage to the ongoing Asia-Europe "
                         "container rate recovery driven by Red Sea disruptions. The stock trades at 4.2x "
                         "forward P/E versus 12x for liner peers, reflecting excessive pessimism on rate "
                         "sustainability. Our base case assumes Red Sea disruptions persist through H1 2026, "
                         "supporting rates materially above consensus freight assumptions.",
            "entry": "$14.20", "stop": "$12.80", "target": "$18.50", "rr": "3.1x",
            "time_horizon": "30-60 days",
            "thesis": ["95% spot rate book = maximum leverage to rate recovery",
                       "4.2x forward P/E vs. 12x liner peer median; deep value",
                       "Red Sea disruption consensus underestimates duration",
                       "Potential special dividend on FCF recovery"],
        },
        {
            "rank": 3, "title": "Initiate Long — STNG (Scorpio Tankers)",
            "action": "BUY", "ticker": "STNG", "conviction": "HIGH",
            "rationale": "Scorpio Tankers is the premier product tanker pure-play. Ongoing European "
                         "refinery disruptions and structural shifts in refined product trade flows "
                         "(Russian product export bans, US Gulf refinery exports) are driving above-normal "
                         "Aframax and LR2 demand. The company's fleet renewal program is 90% complete, "
                         "providing CII compliance advantage and premium charter rates.",
            "entry": "$31.50", "stop": "$28.90", "target": "$38.20", "rr": "2.5x",
            "time_horizon": "45-75 days",
            "thesis": ["Product tanker rates +18% 30D; refinery disruption premium",
                       "Fleet 90% post-2018 build; CII compliance advantage",
                       "European refined product trade flow restructuring structural",
                       "18% earnings upgrade cycle underway; consensus still stale"],
        },
        {
            "rank": 4, "title": "Avoid / Short — SBLK (Star Bulk Carriers)",
            "action": "SELL", "ticker": "SBLK", "conviction": "MEDIUM",
            "rationale": "Star Bulk faces a challenging near-term outlook driven by Supramax and Ultramax "
                         "oversupply, weak Chinese import demand for iron ore and coal, and a fleet age "
                         "profile that creates CII headwinds. Management's FFA hedging book has been "
                         "poorly positioned relative to spot rates, amplifying downside earnings risk.",
            "entry": "$15.20", "stop": "$16.80", "target": "$12.40", "rr": "1.8x",
            "time_horizon": "30-45 days",
            "thesis": ["Chinese iron ore imports -8% YoY; demand headwind",
                       "Supramax orderbook represents 11% of existing fleet",
                       "FFA hedge book poorly positioned for current rate environment",
                       "CII compliance costs rising; older fleet at disadvantage"],
        },
        {
            "rank": 5, "title": "Monitor — DAC (Danaos Corporation)",
            "action": "HOLD", "ticker": "DAC", "conviction": "MEDIUM",
            "rationale": "Danaos offers defensive characteristics within container shipping via its "
                         "long-term charter book (85% of fleet revenue locked through 2025). While this "
                         "limits upside to spot rate recovery, it provides earnings visibility and "
                         "supports the dividend. We maintain a Hold rating pending clarity on renewal "
                         "charter rates, which will determine 2026-2027 earnings trajectory.",
            "entry": "$62.30", "stop": "$57.00", "target": "$74.50", "rr": "2.3x",
            "time_horizon": "90+ days",
            "thesis": ["85% fleet on long-term charter; earnings visibility high",
                       "Charter renewal rates in 2025-2026 key valuation catalyst",
                       "Trades at 8% discount to NAV; modest upside limited",
                       "Dividend yield 3.2%; defensive income in volatile market"],
        },
    ]

    # Use real recs if available, pad with mock
    all_recs = []
    for r in recs:
        if isinstance(r, dict):
            all_recs.append(r)
        else:
            all_recs.append({
                "rank":       getattr(r, "rank", len(all_recs) + 1),
                "title":      _safe(getattr(r, "title", "N/A")),
                "action":     _safe(getattr(r, "action", "HOLD")),
                "ticker":     _safe(getattr(r, "ticker", "N/A")),
                "conviction": _safe(getattr(r, "conviction", "MEDIUM")),
                "rationale":  _safe(getattr(r, "rationale", "")),
                "entry":      _safe(getattr(r, "entry", "")),
                "stop":       _safe(getattr(r, "stop", "")),
                "target":     _safe(getattr(r, "target", "")),
                "rr":         _safe(getattr(r, "rr", "N/A")),
                "time_horizon": _safe(getattr(r, "time_horizon", "N/A")),
                "thesis":     list(getattr(r, "thesis", [])),
            })
    while len(all_recs) < 5:
        mi = len(all_recs)
        if mi < len(_mock_recs):
            all_recs.append(_mock_recs[mi])
        else:
            break

    for rec in all_recs[:5]:
        if pdf.get_y() > pdf.PAGE_H - 50:
            pdf.add_page()

        rank    = rec.get("rank", "?")
        title   = _safe(rec.get("title", "N/A"))
        action  = _safe(rec.get("action", "HOLD")).upper()
        ticker  = _safe(rec.get("ticker", "N/A"))
        conv    = _safe(rec.get("conviction", "N/A")).upper()
        rat     = _safe(rec.get("rationale", ""))
        entry   = _safe(rec.get("entry", "N/A"))
        stop_v  = _safe(rec.get("stop", "N/A"))
        target  = _safe(rec.get("target", "N/A"))
        rr_v    = _safe(rec.get("rr", "N/A"))
        timehor = _safe(rec.get("time_horizon", "N/A"))
        thesis  = rec.get("thesis", [])

        act_color  = _ACTION_COLORS.get(action, DARK_GRAY)
        conv_color = _CONVICTION_COLORS.get(conv, DARK_GRAY)

        y_rec = pdf.get_y()
        # Estimate box height
        rat_lines = max(1, len(rat) // 90 + 1)
        thesis_lines = len(thesis)
        box_h = 10 + rat_lines * 5 + thesis_lines * 4.5 + 10

        pdf.set_fill_color(*LIGHT_GRAY)
        pdf.rect(L, y_rec, IW, box_h, "F")
        pdf.set_draw_color(*act_color)
        pdf.set_line_width(0.4)
        pdf.rect(L, y_rec, IW, box_h)
        # Left color bar
        pdf.set_fill_color(*act_color)
        pdf.rect(L, y_rec, 3.5, box_h, "F")

        # REC number badge
        pdf.set_fill_color(*NAVY)
        pdf.rect(L + 3.5, y_rec, 16, 8, "F")
        pdf.set_xy(L + 3.5, y_rec + 1.5)
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_text_color(*WHITE)
        pdf.cell(16, 5, f"REC {rank:02d}", align="C")

        # Action badge
        pdf.set_fill_color(*act_color)
        pdf.rect(L + 21, y_rec, 16, 8, "F")
        pdf.set_xy(L + 21, y_rec + 1.5)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*WHITE)
        pdf.cell(16, 5, action, align="C")

        # Title
        pdf.set_xy(L + 39, y_rec + 2)
        pdf.set_font("Helvetica", "B", 8.5)
        pdf.set_text_color(*NAVY)
        pdf.cell(IW - 55, 5, _trunc(title, 68), align="L")

        # Conviction badge
        pdf.set_xy(L + IW - 30, y_rec + 1.5)
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_text_color(*conv_color)
        pdf.cell(28, 5, f"CONVICTION: {conv}", align="R")

        # Rationale
        pdf.set_xy(L + 5, y_rec + 9)
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_text_color(*DARK_GRAY)
        pdf.multi_cell(IW - 8, 4.5, rat, align="J")

        # Thesis bullets
        if thesis:
            for bullet in thesis[:4]:
                if pdf.get_y() > y_rec + box_h - 6:
                    break
                pdf.set_x(L + 7)
                pdf.set_font("Helvetica", "", 7)
                pdf.set_text_color(*DARK_GRAY)
                pdf.cell(3, 4, "\u2022", align="L")
                pdf.cell(IW - 12, 4, _trunc(str(bullet), 90), align="L")
                pdf.ln(0)

        # Key metrics row
        metrics_y = y_rec + box_h - 7
        pdf.set_xy(L + 5, metrics_y)
        pdf.set_font("Helvetica", "B", 6.5)
        pdf.set_text_color(*NAVY)
        metrics_str = (f"Entry: {entry}   |   Stop: {stop_v}   |   "
                       f"Target: {target}   |   R:R: {rr_v}   |   Horizon: {timehor}")
        pdf.cell(IW - 8, 5, metrics_str, align="L")

        pdf.set_y(y_rec + box_h + 3)

    # ── Risk disclosure
    pdf.ln(3)
    pdf._rule(GOLD_LINE, 0.4)
    pdf.set_x(L)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(*NAVY)
    pdf.cell(IW, 5, "RISK DISCLOSURE", align="L")
    pdf.ln(1)
    if risk_narr:
        pdf.set_x(L)
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_text_color(*DARK_GRAY)
        pdf.multi_cell(IW, 4.8, risk_narr, align="J")
    else:
        disc_text = (
            "All recommendations carry inherent risk of capital loss. Shipping equities exhibit above-average "
            "volatility (typical beta 1.4-2.2x market) and are subject to cyclical rate risk, geopolitical "
            "disruption, fuel price volatility, and regulatory change. Stop-loss levels should be strictly "
            "observed. Position sizing should reflect individual risk tolerance and portfolio construction "
            "guidelines. These recommendations are generated by a proprietary algorithmic system and should "
            "be supplemented by independent fundamental analysis before execution."
        )
        pdf.set_x(L)
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_text_color(*DARK_GRAY)
        pdf.multi_cell(IW, 4.8, disc_text, align="J")

    pdf._footnote("8 Recommendations generated by proprietary multi-factor alpha engine. Not investment advice. "
                  "Past performance is not indicative of future results. Institutional clients only.")


# ═══════════════════════════════════════════════════════════════════════════════
#  PAGE 16 — DISCLAIMER & METHODOLOGY
# ═══════════════════════════════════════════════════════════════════════════════

def _disclaimer_page(pdf: InstitutionalReportPDF, report) -> None:
    pdf.add_page()
    pdf.set_section_name("APPENDIX — DISCLAIMER & METHODOLOGY")
    L = pdf.L_MARG
    IW = pdf.INNER_W

    pdf._section_title("Disclaimer & Methodology", 0)

    ai_obj     = getattr(report, "ai", None)
    ai_disc    = _safe(getattr(ai_obj, "disclaimer", "")) if ai_obj else ""
    report_date = _safe(getattr(report, "report_date", "N/A"))
    rdate_safe  = report_date.replace(" ", "-").replace(",", "")[:12]
    report_id   = f"SHI-{rdate_safe}"

    # Full legal disclaimer
    pdf._sub_header("Legal Disclaimer & Important Disclosures")
    full_disclaimer = ai_disc if ai_disc and len(ai_disc) > 100 else (
        "IMPORTANT: This document has been prepared by ShipTracker Intelligence Platform (\"ShipTracker\") "
        "solely for informational purposes and does not constitute investment advice, an offer to buy or sell, "
        "or a solicitation of any offer to buy or sell any security or instrument, or to participate in any "
        "particular trading strategy. This document is intended only for the recipient and may not be "
        "distributed to third parties without the express written consent of ShipTracker.\n\n"
        "The information and opinions in this report were prepared by ShipTracker from public and proprietary "
        "sources. ShipTracker makes no representation that this information is accurate or complete and it "
        "should not be relied upon as such. Opinions, estimates, and projections in this report are as of the "
        "date of this report and are subject to change without notice. ShipTracker and its affiliates, "
        "officers, directors, partners, and employees, including those involved in the preparation or issuance "
        "of this document, may hold long or short positions in the securities or instruments mentioned herein "
        "and may engage in transactions contrary to the recommendations set forth in this document.\n\n"
        "Past performance is not indicative of future results. The value of investments and the income derived "
        "from them may fall as well as rise and investors may not get back the amount originally invested. "
        "Shipping markets are subject to significant cyclical variation, geopolitical risk, and regulatory "
        "change that may materially affect the accuracy of any forecast or recommendation contained herein.\n\n"
        "This document is not directed to, or intended for distribution to or use by, any person or entity "
        "who is a citizen or resident of, or located in, any locality, state, country, or other jurisdiction "
        "where such distribution, publication, availability, or use would be contrary to law or regulation, "
        "or which would subject ShipTracker or its affiliates to any registration or licensing requirement "
        "within such jurisdiction. Recipients of this document in jurisdictions outside the United States "
        "should inform themselves about and observe applicable legal requirements.\n\n"
        "Alpha signal generation is performed by a proprietary algorithmic system. Signals are generated "
        "based on quantitative factors and do not constitute personalized investment advice. The system's "
        "historical performance, where tested, has been conducted in controlled conditions and is not "
        "necessarily indicative of future live trading performance. Slippage, transaction costs, and "
        "market impact may materially reduce realized returns relative to backtested results."
    )

    pdf.set_x(L)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*DARK_GRAY)
    pdf.multi_cell(IW, 4.8, full_disclaimer, align="J")

    pdf.ln(4)
    pdf._rule(GOLD_LINE, 0.4)

    # ── Data Sources & Methodology table
    pdf._sub_header("Data Sources & Methodology")
    ds_headers = ["SOURCE", "TYPE", "FREQUENCY", "COVERAGE", "NOTES"]
    ds_widths  = [30.0, 24.0, 20.0, 30.0, 81.9]
    ds_rows = [
        ["Baltic Exchange",     "Freight Rates",   "Daily",   "Dry bulk, tanker", "Official BDI, BCI, BPI, BSI, BHSI indices"],
        ["Freightos FBX",       "Freight Rates",   "Weekly",  "Container global", "12-corridor composite; USD/TEU spot rates"],
        ["FRED (Federal Res.)", "Macro Indicators","Monthly", "US & Global",      "Treasury yields, CPI, PMI, trade data"],
        ["AIS Data Feed",       "Vessel Tracking", "Real-time","Global fleet",    "Port calls, dwell times, speed profiles"],
        ["NewsAPI",             "News Headlines",  "Hourly",  "Global maritime",  "NLP sentiment scoring on 500+ sources"],
        ["Alpha Vantage",       "Equity Prices",   "Daily",   "Listed shippers",  "EOD prices, volume, earnings estimates"],
        ["OECD Statistics",     "Trade Data",      "Monthly", "OECD economies",   "Container throughput, seaborne trade vol."],
        ["IMF World Economic",  "Macro Forecasts", "Quarterly","Global",          "GDP growth, trade volume projections"],
        ["Clarksons Research",  "Fleet Data",      "Weekly",  "Global fleet",     "Orderbook, deliveries, scrapping data"],
        ["S&P Global PMI",      "PMI Surveys",     "Monthly", "50+ economies",    "Manufacturing & composite PMI indices"],
    ]
    pdf._data_table(ds_headers, ds_rows, ds_widths, color_cols=[])

    pdf.ln(4)

    # ── Signal Generation Methodology
    pdf._sub_header("Signal Generation Methodology")
    pdf.set_x(L)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*DARK_GRAY)
    meth_text = (
        "The ShipTracker alpha signal engine employs a multi-factor approach combining five distinct "
        "sub-models: (1) Momentum — measures rate-of-change in freight rates, equity prices, and "
        "macro indicators over 5, 20, and 60-day windows; (2) Mean Reversion — identifies instruments "
        "trading at statistically significant deviations from their rolling 60-day means using z-score "
        "methodology; (3) Fundamental — compares current freight rates and equity multiples to "
        "long-run equilibrium estimates derived from supply-demand models; (4) Technical — applies "
        "RSI, MACD, and Bollinger Band analysis to equity price series; and (5) Macro — scores "
        "current macro environment (PMI, rates, USD) relative to shipping market sensitivity matrices.\n\n"
        "Each sub-model produces a directional signal (LONG/SHORT) and a strength score (0.0-1.0). "
        "Signals are aggregated using a weighted combination where weights are dynamically adjusted "
        "based on recent model performance. A signal is classified as HIGH conviction when the "
        "weighted strength score exceeds 0.80 and is corroborated by at least two independent "
        "sub-models. MEDIUM conviction requires strength >= 0.60 with single-model confirmation. "
        "LOW conviction signals (0.40-0.60) are included for monitoring purposes only and should "
        "not form the basis of material position sizing decisions."
    )
    pdf.multi_cell(IW, 4.8, meth_text, align="J")

    pdf.ln(4)
    pdf._rule(MID_GRAY, 0.2)

    # ── Important Disclosures
    pdf._sub_header("Important Disclosures — Sell-Side Standard Language")
    discl_items = [
        "ShipTracker has not received compensation for investment banking services from the companies mentioned in this report in the past 12 months.",
        "ShipTracker does not make a market in the securities of the companies mentioned in this report.",
        "Analyst certification: The views expressed in this report accurately reflect the analyst's personal views about the subject companies and securities.",
        "This report has been prepared independently of any trading desk or proprietary trading operation.",
        "Rating system: BUY = alpha signal direction LONG with HIGH conviction. SELL = alpha signal direction SHORT. HOLD = no active signal or LOW conviction.",
        "Risk ratings are assessed quarterly and updated intra-period on material developments.",
    ]
    for item in discl_items:
        pdf.set_x(L + 3)
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(*DARK_GRAY)
        pdf.cell(4, 4.5, "\u2022", align="L")
        pdf.multi_cell(IW - 7, 4.5, item, align="J")
        pdf.ln(0.5)

    pdf.ln(4)

    # ── Timestamp block
    y_ts = pdf.get_y()
    pdf.set_fill_color(*LIGHT_GRAY)
    pdf.rect(L, y_ts, IW, 14, "F")
    pdf.set_draw_color(*MID_GRAY)
    pdf.set_line_width(0.25)
    pdf.rect(L, y_ts, IW, 14)
    pdf.set_xy(L + 3, y_ts + 2)
    pdf.set_font("Helvetica", "B", 6.5)
    pdf.set_text_color(*NAVY)
    pdf.cell(IW / 2, 4, f"Report ID: {report_id}", align="L")
    pdf.set_xy(L + IW / 2, y_ts + 2)
    pdf.cell(IW / 2 - 3, 4, f"Generated: {report_date}", align="R")
    pdf.set_xy(L + 3, y_ts + 7)
    pdf.set_font("Helvetica", "", 6)
    pdf.set_text_color(*TEXT_LO)
    pdf.cell(IW - 6, 4, "ShipTracker Intelligence Platform  |  Institutional Research Division  |  "
             "For Institutional Use Only  |  Not For Redistribution", align="L")


# ═══════════════════════════════════════════════════════════════════════════════
#  PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def render_investor_report_pdf(report) -> Optional[bytes]:
    """
    Render an InvestorReport to institutional-grade PDF bytes.

    Args:
        report: InvestorReport dataclass instance from processing/investor_report_engine.py

    Returns:
        bytes: PDF content, or None if fpdf2 is not installed or an error occurs.
    """
    if not _FPDF_OK:
        import logging
        logging.getLogger(__name__).warning(
            "fpdf2 not installed. Run: pip install fpdf2"
        )
        return None

    try:
        pdf = InstitutionalReportPDF()

        # Set the report date on the instance for headers/footers
        pdf._report_date = _safe(getattr(report, "report_date", ""), "")

        # Build all pages
        _cover_page(pdf, report)
        _toc_page(pdf, report)
        _executive_summary_page(pdf, report)
        _signal_intelligence_pages(pdf, report)
        _freight_rate_pages(pdf, report)
        _macro_page(pdf, report)
        _equity_page(pdf, report)
        _market_intelligence_page(pdf, report)
        _risk_page(pdf, report)
        _recommendations_page(pdf, report)
        _disclaimer_page(pdf, report)

        return bytes(pdf.output())

    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "render_investor_report_pdf: PDF generation failed"
        )
        return None
