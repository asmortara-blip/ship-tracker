"""
utils/investor_report_pdf.py
────────────────────────────
Goldman Sachs / Bloomberg Intelligence quality PDF renderer for InvestorReport.

Produces a 10-page institutional-grade research brief: dark navy background,
GOLD accents, data-rich tables, and dense prose — the kind of document a managing
director would hand to an institutional client.

Usage:
    from utils.investor_report_pdf import render_investor_report_pdf
    pdf_bytes = render_investor_report_pdf(report)
    st.download_button("Download PDF", pdf_bytes, "report.pdf", "application/pdf")

Dependencies: fpdf2 (pip install fpdf2)
No external fonts required — uses Helvetica throughout.
"""
from __future__ import annotations

import io
import traceback
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, List, Optional, Tuple

# ── fpdf2 ────────────────────────────────────────────────────────────────────
try:
    from fpdf import FPDF
    _FPDF_OK = True
except ImportError:
    _FPDF_OK = False

# ── pdf_charts (optional) ────────────────────────────────────────────────────
try:
    import utils.pdf_charts as pdf_charts  # type: ignore
    _CHARTS_OK = True
except Exception:
    _CHARTS_OK = False

if TYPE_CHECKING:
    from processing.investor_report_engine import InvestorReport


# ═══════════════════════════════════════════════════════════════════════════════
#  COLOR PALETTE — Goldman-inspired dark institutional
# ═══════════════════════════════════════════════════════════════════════════════

# Page / surface
INK_BG      = (13,  27,  42)    # #0D1B2A  deep navy — page background
INK_SURFACE = (19,  34,  55)    # #132237  section backgrounds
INK_CARD    = (26,  46,  69)    # #1A2E45  card / table backgrounds
INK_BORDER  = (30,  58,  95)    # #1E3A5F  borders / rules

# Accent
GOLD        = (201, 168, 76)    # #C9A84C  primary gold accent
STEEL       = (46,  134, 193)   # #2E86C1  steel blue
TEAL        = (26,  188, 156)   # #1ABC9C  bullish / positive
CRIMSON     = (231, 76,  60)    # #E74C3C  bearish / negative
AMBER       = (243, 156, 18)    # #F39C12  neutral / caution
PURPLE      = (155, 89,  182)   # #9B59B6  convergence

# Text
TEXT_HI     = (236, 240, 241)   # #ECF0F1  primary text (near white)
TEXT_MID    = (149, 165, 166)   # #95A5A6  secondary text
TEXT_LO     = (100, 116, 130)   # muted text
WHITE       = (255, 255, 255)
BLACK       = (0,   0,   0)

_RISK_COLORS = {
    "LOW":      TEAL,
    "MODERATE": AMBER,
    "HIGH":     CRIMSON,
    "CRITICAL": (185, 28, 28),
}

_CONVICTION_COLORS = {
    "HIGH":   TEAL,
    "MEDIUM": AMBER,
    "LOW":    CRIMSON,
}

_ACTION_COLORS = {
    "BUY":     TEAL,
    "LONG":    TEAL,
    "SELL":    CRIMSON,
    "SHORT":   CRIMSON,
    "HOLD":    AMBER,
    "MONITOR": STEEL,
    "WATCH":   STEEL,
}


# ═══════════════════════════════════════════════════════════════════════════════
#  UTILITY FUNCTIONS
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
    return {"BULLISH": TEAL, "BEARISH": CRIMSON, "NEUTRAL": TEXT_MID,
            "MIXED": AMBER}.get(str(label).upper(), TEXT_LO)


def _change_color(pct: float) -> tuple:
    try:
        v = float(pct)
        if v > 0:
            return TEAL
        if v < 0:
            return CRIMSON
        return TEXT_LO
    except (TypeError, ValueError):
        return TEXT_LO


def _score_color(score: float) -> tuple:
    try:
        v = float(score)
        if v >= 0.70:
            return TEAL
        if v >= 0.40:
            return AMBER
        return CRIMSON
    except (TypeError, ValueError):
        return TEXT_LO


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
    """Return True if the string looks like a number (for right-alignment)."""
    cleaned = str(s).strip().lstrip("+-$").rstrip("%,")
    try:
        float(cleaned.replace(",", ""))
        return True
    except ValueError:
        return False


# ═══════════════════════════════════════════════════════════════════════════════
#  PDF CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class InstitutionalReportPDF(FPDF):
    """
    fpdf2 subclass styled for Goldman-tier institutional research output.

    Letter size (215.9 × 279.4 mm), 15 mm margins, dark navy page background.
    """

    PAGE_W  = 215.9
    PAGE_H  = 279.4
    L_MARG  = 15.0
    R_MARG  = 15.0
    T_MARG  = 18.0
    B_MARG  = 18.0
    INNER_W = 215.9 - 15.0 - 15.0   # 185.9 mm

    # Set at construction so footer can reference it
    _report_date: str = ""

    def __init__(self) -> None:
        super().__init__(orientation="P", unit="mm", format="Letter")
        self.set_margins(self.L_MARG, self.T_MARG, self.R_MARG)
        self.set_auto_page_break(auto=True, margin=self.B_MARG)
        self.alias_nb_pages()  # enables {nb} substitution for total page count

    # ── Header ───────────────────────────────────────────────────────────────

    def header(self) -> None:
        if self.page_no() == 1:
            return  # Cover has its own full-bleed header

        # Background strip
        self.set_fill_color(*INK_SURFACE)
        self.rect(0, 0, self.PAGE_W, 11, "F")

        # Gold bottom rule on header
        self.set_draw_color(*GOLD)
        self.set_line_width(0.4)
        self.line(0, 11, self.PAGE_W, 11)

        # Firm name — left
        self.set_xy(self.L_MARG, 3.5)
        self.set_font("Helvetica", "B", 7)
        self.set_text_color(*TEXT_MID)
        self.cell(80, 4, "GLOBAL SHIPPING INTELLIGENCE", align="L")

        # CONFIDENTIAL — center
        self.set_xy(self.L_MARG + 60, 3.5)
        self.set_font("Helvetica", "B", 7)
        self.set_text_color(*TEXT_LO)
        self.cell(self.INNER_W - 120, 4, "CONFIDENTIAL", align="C")

        # Page X of {nb} — right
        self.set_xy(self.PAGE_W - self.R_MARG - 35, 3.5)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*TEXT_LO)
        self.cell(35, 4, f"PAGE {self.page_no()}", align="R")

    # ── Footer ───────────────────────────────────────────────────────────────

    def footer(self) -> None:
        if self.page_no() == 1:
            return  # Cover has its own footer

        # Gold top rule
        self.set_draw_color(*GOLD)
        self.set_line_width(0.35)
        self.line(self.L_MARG, self.PAGE_H - 12, self.PAGE_W - self.R_MARG, self.PAGE_H - 12)

        # "FOR INSTITUTIONAL USE ONLY" — center
        self.set_xy(self.L_MARG, self.PAGE_H - 10)
        self.set_font("Helvetica", "B", 6)
        self.set_text_color(*TEXT_LO)
        self.cell(self.INNER_W * 0.6, 4, "FOR INSTITUTIONAL USE ONLY — NOT FOR REDISTRIBUTION", align="C")

        # Date — right
        self.set_xy(self.PAGE_W - self.R_MARG - 50, self.PAGE_H - 10)
        self.set_font("Helvetica", "", 6)
        self.set_text_color(*TEXT_LO)
        self.cell(50, 4, self._report_date, align="R")

    # ── Section header bar ───────────────────────────────────────────────────

    def section_header(self, title: str, subtitle: str = "") -> None:
        """INK_SURFACE bg, 3mm GOLD left border, title in GOLD Bold 12pt."""
        y = self.get_y()
        h = 11 if not subtitle else 16
        self.set_fill_color(*INK_SURFACE)
        self.rect(self.L_MARG, y, self.INNER_W, h, "F")
        # Gold left border (3mm)
        self.set_fill_color(*GOLD)
        self.rect(self.L_MARG, y, 3, h, "F")
        # Title
        self.set_xy(self.L_MARG + 6, y + 2)
        self.set_font("Helvetica", "B", 12)
        self.set_text_color(*GOLD)
        self.cell(self.INNER_W - 10, 7, title.upper(), align="L")
        # Subtitle
        if subtitle:
            self.set_xy(self.L_MARG + 6, y + 9)
            self.set_font("Helvetica", "", 8)
            self.set_text_color(*TEXT_MID)
            self.cell(self.INNER_W - 10, 5, subtitle, align="L")
        self.set_y(y + h + 4)

    # ── KPI box ──────────────────────────────────────────────────────────────

    def kpi_box(self, x: float, y: float, w: float, h: float,
                label: str, value: str, sub: str = "", color: tuple = None) -> None:
        """INK_CARD bg, GOLD top border 1mm, label 7pt TEXT_MID, value bold 14pt."""
        color = color or GOLD
        self.set_fill_color(*INK_CARD)
        self.rect(x, y, w, h, "F")
        # Top color border
        self.set_fill_color(*color)
        self.rect(x, y, w, 1.2, "F")
        # Label
        self.set_xy(x + 2.5, y + 3)
        self.set_font("Helvetica", "B", 6.5)
        self.set_text_color(*TEXT_LO)
        self.cell(w - 5, 4, label.upper()[:28], align="L")
        # Value
        self.set_xy(x + 2.5, y + 8)
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(*color)
        val_str = str(value)[:12]
        # Shrink font if value is long
        font_sz = 14 if len(val_str) <= 8 else 11 if len(val_str) <= 11 else 9
        self.set_font("Helvetica", "B", font_sz)
        self.cell(w - 5, 8, val_str, align="L")
        # Sub-label
        if sub:
            self.set_xy(x + 2.5, y + h - 5)
            self.set_font("Helvetica", "", 6)
            self.set_text_color(*TEXT_LO)
            self.cell(w - 5, 4, str(sub)[:26], align="L")

    # ── Professional table ───────────────────────────────────────────────────

    def data_table(self, headers: List[str], rows: List[List[str]],
                   col_widths: List[float], row_colors: List[tuple] = None,
                   header_bg: tuple = None) -> float:
        """
        Professional table with alternating INK_SURFACE/INK_CARD rows.
        Right-aligns number columns, left-aligns text. Returns ending Y.
        """
        header_bg = header_bg or INK_BORDER
        row_h = 5.5

        # Header row
        y = self.get_y()
        self.set_fill_color(*header_bg)
        self.rect(self.L_MARG, y, self.INNER_W, 6.5, "F")
        # Gold underline on header
        self.set_draw_color(*GOLD)
        self.set_line_width(0.3)
        self.line(self.L_MARG, y + 6.5, self.L_MARG + self.INNER_W, y + 6.5)

        x = self.L_MARG
        self.set_font("Helvetica", "B", 7)
        self.set_text_color(*TEXT_HI)
        for hdr, cw in zip(headers, col_widths):
            self.set_xy(x + 1.5, y + 1.5)
            self.cell(cw - 3, 3.5, str(hdr).upper(), align="L")
            x += cw
        self.set_y(y + 6.5)

        # Data rows
        for i, row in enumerate(rows):
            y = self.get_y()
            if self.will_page_break(row_h + 2):
                self.add_page()
                y = self.get_y()

            # Alternating fill
            bg = INK_SURFACE if i % 2 == 0 else INK_CARD
            self.set_fill_color(*bg)
            self.rect(self.L_MARG, y, self.INNER_W, row_h, "F")

            # Per-row color marker on left edge (optional)
            if row_colors and i < len(row_colors) and row_colors[i]:
                self.set_fill_color(*row_colors[i])
                self.rect(self.L_MARG, y, 1.5, row_h, "F")

            x = self.L_MARG
            for j, (cell_val, cw) in enumerate(zip(row, col_widths)):
                self.set_xy(x + 1.5, y + 1)
                txt = str(cell_val)
                is_num = _is_number(txt)
                align = "R" if is_num else "L"
                if j == 0:
                    self.set_font("Helvetica", "B", 7)
                    self.set_text_color(*TEXT_HI)
                else:
                    self.set_font("Helvetica", "", 7)
                    self.set_text_color(*TEXT_MID)
                # Truncate so it doesn't overflow
                max_chars = max(4, int(cw / 1.65))
                disp = txt[:max_chars]
                self.cell(cw - 3, 3.5, disp, align=align)
                x += cw
            self.set_y(y + row_h)

        return self.get_y()

    # ── Insight card ─────────────────────────────────────────────────────────

    def insight_card(self, rank: int, title: str, detail: str, score: float,
                     action: str, category: str,
                     ports: str = "", routes: str = "", stocks: str = "") -> None:
        """Numbered insight card with colored category badge and score bar."""
        if self.will_page_break(28):
            self.add_page()
        y = self.get_y()
        card_h = 28
        s_color = _score_color(score)
        act_color = _ACTION_COLORS.get(str(action).upper(), STEEL)

        # Card background
        self.set_fill_color(*INK_CARD)
        self.rect(self.L_MARG, y, self.INNER_W, card_h, "F")
        # Left border colored by score
        self.set_fill_color(*s_color)
        self.rect(self.L_MARG, y, 2.5, card_h, "F")

        # Rank circle (faux)
        self.set_fill_color(*INK_BORDER)
        self.rect(self.L_MARG + 5, y + 3, 7, 7, "F")
        self.set_xy(self.L_MARG + 5, y + 3.5)
        self.set_font("Helvetica", "B", 7)
        self.set_text_color(*GOLD)
        self.cell(7, 6, str(rank), align="C")

        # Title
        self.set_xy(self.L_MARG + 15, y + 2.5)
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*TEXT_HI)
        self.cell(self.INNER_W - 75, 5, str(title)[:60], align="L")

        # Action badge
        ax = self.L_MARG + self.INNER_W - 44
        self.set_fill_color(*act_color)
        self.rect(ax, y + 2, 22, 5.5, "F")
        self.set_xy(ax, y + 2.5)
        self.set_font("Helvetica", "B", 6.5)
        self.set_text_color(*WHITE)
        self.cell(22, 4.5, str(action).upper()[:10], align="C")

        # Category badge
        cx = self.L_MARG + self.INNER_W - 20
        self.set_fill_color(*PURPLE)
        self.rect(cx, y + 2, 17, 5.5, "F")
        self.set_xy(cx, y + 2.5)
        self.set_font("Helvetica", "B", 6)
        self.set_text_color(*WHITE)
        self.cell(17, 4.5, str(category)[:10].upper(), align="C")

        # Score bar track
        bar_x = self.L_MARG + 15
        bar_y = y + 9
        bar_w = self.INNER_W - 55
        bar_h = 2.5
        self.set_fill_color(*INK_BORDER)
        self.rect(bar_x, bar_y, bar_w, bar_h, "F")
        fill_w = bar_w * _clamp(float(score))
        if fill_w > 0:
            self.set_fill_color(*s_color)
            self.rect(bar_x, bar_y, fill_w, bar_h, "F")

        # Score label
        self.set_xy(bar_x + bar_w + 2, y + 7.5)
        self.set_font("Helvetica", "B", 7.5)
        self.set_text_color(*s_color)
        self.cell(18, 4, f"{score * 100:.0f}%", align="L")

        # Detail text
        self.set_xy(self.L_MARG + 15, y + 13.5)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*TEXT_MID)
        self.cell(self.INNER_W - 20, 4, str(detail)[:110], align="L")

        # Ports / Routes / Stocks
        meta_parts = []
        if ports:
            meta_parts.append(f"Ports: {ports}")
        if routes:
            meta_parts.append(f"Routes: {routes}")
        if stocks:
            meta_parts.append(f"Stocks: {stocks}")
        if meta_parts:
            self.set_xy(self.L_MARG + 15, y + 19)
            self.set_font("Helvetica", "", 6.5)
            self.set_text_color(*TEXT_LO)
            self.cell(self.INNER_W - 20, 4, "  |  ".join(meta_parts)[:110], align="L")

        self.set_y(y + card_h + 3)

    # ── Recommendation card ───────────────────────────────────────────────────

    def rec_card(self, rec: dict) -> None:
        """Full-width recommendation card with action-colored left border."""
        rank      = _safe(rec.get("rank", ""))
        title     = _safe(rec.get("title", "Recommendation"))
        action    = _safe(rec.get("action", "MONITOR")).upper()
        ticker    = _safe(rec.get("ticker", "—"))
        conviction= _safe(rec.get("conviction", "MEDIUM")).upper()
        exp_ret   = float(rec.get("expected_return", 0.0) or 0.0)
        horizon   = _safe(rec.get("time_horizon", "—"))
        risk_rat  = _safe(rec.get("risk_rating", "—"))
        rationale = _safe(rec.get("rationale", ""))
        entry     = rec.get("entry", 0.0) or 0.0
        target    = rec.get("target", 0.0) or 0.0
        stop      = rec.get("stop", 0.0) or 0.0

        act_color  = _ACTION_COLORS.get(action, STEEL)
        conv_color = _CONVICTION_COLORS.get(conviction, AMBER)

        has_price = bool(entry)
        card_h = 38 if has_price else 30

        if self.will_page_break(card_h + 4):
            self.add_page()
        y = self.get_y()

        # Card background
        self.set_fill_color(*INK_CARD)
        self.rect(self.L_MARG, y, self.INNER_W, card_h, "F")
        # Action-colored left border
        self.set_fill_color(*act_color)
        self.rect(self.L_MARG, y, 3.5, card_h, "F")
        # Subtle top border
        self.set_draw_color(*INK_BORDER)
        self.set_line_width(0.2)
        self.rect(self.L_MARG, y, self.INNER_W, card_h)

        # ── Row 1: rank + action badge + title + ticker ──
        # Rank circle
        self.set_fill_color(*INK_BORDER)
        self.rect(self.L_MARG + 7, y + 2.5, 8, 8, "F")
        self.set_xy(self.L_MARG + 7, y + 3)
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(*GOLD)
        self.cell(8, 7, str(rank), align="C")

        # Action badge
        self.set_fill_color(*act_color)
        self.rect(self.L_MARG + 18, y + 3, 22, 6, "F")
        self.set_xy(self.L_MARG + 18, y + 3.5)
        self.set_font("Helvetica", "B", 7.5)
        self.set_text_color(*WHITE)
        self.cell(22, 5, action[:8], align="C")

        # Title
        self.set_xy(self.L_MARG + 43, y + 3)
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*TEXT_HI)
        self.cell(self.INNER_W - 90, 6, str(title)[:55], align="L")

        # Ticker
        self.set_xy(self.L_MARG + self.INNER_W - 40, y + 3)
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*GOLD)
        self.cell(38, 6, ticker, align="R")

        # ── Row 2: stats ──
        stats_y = y + 12
        stats = [
            ("CONVICTION",     conviction,            conv_color),
            ("TIME HORIZON",   horizon,               TEXT_MID),
            ("EXP. RETURN",    f"{exp_ret:+.1f}%",    _change_color(exp_ret)),
            ("RISK RATING",    risk_rat,               _RISK_COLORS.get(risk_rat.upper(), AMBER)),
        ]
        col_w = self.INNER_W / 4
        for k, (lbl, val, col) in enumerate(stats):
            sx = self.L_MARG + k * col_w
            self.set_xy(sx + 4, stats_y)
            self.set_font("Helvetica", "", 6)
            self.set_text_color(*TEXT_LO)
            self.cell(col_w - 4, 3.5, lbl, align="L")
            self.set_xy(sx + 4, stats_y + 3.5)
            self.set_font("Helvetica", "B", 8)
            self.set_text_color(*col)
            self.cell(col_w - 4, 4.5, str(val)[:16], align="L")

        # ── Row 3: price levels (if available) ──
        if has_price:
            price_y = y + 21.5
            self.set_fill_color(*INK_BORDER)
            self.rect(self.L_MARG + 4, price_y, self.INNER_W - 8, 6, "F")
            px_items = [
                (f"ENTRY: {_fmt_price(entry)}",  TEXT_HI),
                (f"TARGET: {_fmt_price(target)}", TEAL),
                (f"STOP: {_fmt_price(stop)}",     CRIMSON),
            ]
            for k, (ptxt, pcol) in enumerate(px_items):
                self.set_xy(self.L_MARG + 6 + k * 50, price_y + 1)
                self.set_font("Helvetica", "B", 7)
                self.set_text_color(*pcol)
                self.cell(48, 4, ptxt, align="L")

        # ── Rationale ──
        rat_y = y + (29 if has_price else 21)
        self.set_xy(self.L_MARG + 4, rat_y)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*TEXT_MID)
        self.cell(self.INNER_W - 8, 4, str(rationale)[:170], align="L")

        self.set_y(y + card_h + 3)

    # ── Embed chart ───────────────────────────────────────────────────────────

    def embed_chart(self, chart_bytes: bytes, x: float = None, y: float = None,
                    w: float = 180, h: float = None) -> None:
        """Embed PNG bytes directly using fpdf2's image() with io.BytesIO."""
        if not chart_bytes:
            return
        buf = io.BytesIO(chart_bytes)
        x = x if x is not None else self.L_MARG
        y = y if y is not None else self.get_y()
        if h:
            self.image(buf, x=x, y=y, w=w, h=h)
        else:
            self.image(buf, x=x, y=y, w=w)
        self.set_y(y + (h or w * 0.5) + 4)

    # ── Prose block ───────────────────────────────────────────────────────────

    def prose(self, text: str, indent: float = 0, size: int = 8,
              color: tuple = None) -> None:
        """Wrapped justified prose text."""
        color = color or TEXT_MID
        self.set_font("Helvetica", "", size)
        self.set_text_color(*color)
        self.set_x(self.L_MARG + indent)
        self.multi_cell(self.INNER_W - indent, 5, str(text), align="J")
        self.ln(1.5)

    # ── Horizontal rule ──────────────────────────────────────────────────────

    def rule(self, color: tuple = None, thickness: float = 0.3) -> None:
        color = color or INK_BORDER
        y = self.get_y()
        self.set_draw_color(*color)
        self.set_line_width(thickness)
        self.line(self.L_MARG, y, self.PAGE_W - self.R_MARG, y)
        self.ln(2)

    # ── Two-column layout ────────────────────────────────────────────────────

    def two_col(self, left_fn: Callable, right_fn: Callable, gap: float = 5) -> None:
        """Run left_fn in left col, right_fn in right col, advance Y past both."""
        col_w = (self.INNER_W - gap) / 2
        start_y = self.get_y()

        # Left column
        self.set_x(self.L_MARG)
        left_fn(col_w)
        left_end_y = self.get_y()

        # Right column
        self.set_xy(self.L_MARG + col_w + gap, start_y)
        right_fn(col_w)
        right_end_y = self.get_y()

        self.set_y(max(left_end_y, right_end_y) + 2)

    # ── Section label (sub-heading) ───────────────────────────────────────────

    def sub_heading(self, text: str, color: tuple = None) -> None:
        color = color or TEXT_HI
        y = self.get_y()
        self.set_fill_color(*INK_SURFACE)
        self.rect(self.L_MARG, y, self.INNER_W, 6.5, "F")
        self.set_xy(self.L_MARG + 3, y + 1.5)
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*color)
        self.cell(self.INNER_W - 6, 4, str(text).upper(), align="L")
        self.set_y(y + 6.5 + 2)

    # ── Fill page background ──────────────────────────────────────────────────

    def fill_page_bg(self, color: tuple = None) -> None:
        color = color or INK_BG
        self.set_fill_color(*color)
        self.rect(0, 0, self.PAGE_W, self.PAGE_H, "F")

    # ── Mini stat box (3-up row) ──────────────────────────────────────────────

    def mini_stat(self, x: float, y: float, w: float, h: float,
                  label: str, value: str, color: tuple = None) -> None:
        color = color or STEEL
        self.set_fill_color(*INK_CARD)
        self.rect(x, y, w, h, "F")
        self.set_fill_color(*color)
        self.rect(x, y + h - 1, w, 1, "F")
        self.set_xy(x + 2, y + 2)
        self.set_font("Helvetica", "", 6.5)
        self.set_text_color(*TEXT_LO)
        self.cell(w - 4, 4, str(label).upper(), align="C")
        self.set_xy(x + 2, y + 7)
        self.set_font("Helvetica", "B", 16)
        self.set_text_color(*color)
        self.cell(w - 4, 9, str(value), align="C")


# ═══════════════════════════════════════════════════════════════════════════════
#  PAGE BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════

def _page_cover(pdf: InstitutionalReportPDF, report) -> None:
    """Page 1: Cover — full dark navy, Goldman-style."""
    pdf.add_page()
    pdf.fill_page_bg()

    W  = pdf.PAGE_W
    H  = pdf.PAGE_H
    LM = pdf.L_MARG

    sent    = report.sentiment
    sent_label = _safe(getattr(sent, "overall_label", "NEUTRAL")).upper()
    sent_score = float(getattr(sent, "overall_score", 0.0) or 0.0)
    sent_color = _sentiment_color(sent_label)

    # ── TOP STRIP (full-width, height 45mm) ──────────────────────────────────
    strip_h = 45
    pdf.set_fill_color(*INK_SURFACE)
    pdf.rect(0, 0, W, strip_h, "F")
    # Gold accent line at very top
    pdf.set_fill_color(*GOLD)
    pdf.rect(0, 0, W, 1.5, "F")

    # LEFT 60% — identity block
    left_w = W * 0.60
    pdf.set_xy(LM, 6)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(*GOLD)
    pdf.cell(left_w - LM, 10, "GLOBAL SHIPPING INTELLIGENCE", align="L")
    pdf.set_xy(LM, 16)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*TEXT_MID)
    pdf.cell(left_w - LM, 6, "INSTITUTIONAL MARKET BRIEFING", align="L")

    # Thin gold rule
    pdf.set_draw_color(*GOLD)
    pdf.set_line_width(0.5)
    pdf.line(LM, 24, left_w - 4, 24)

    # Report date
    pdf.set_xy(LM, 26)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*TEXT_MID)
    pdf.cell(left_w - LM, 5, _safe(report.report_date), align="L")

    # Classification
    pdf.set_xy(LM, 33)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(*CRIMSON)
    pdf.cell(left_w - LM, 4, "CONFIDENTIAL — FOR INSTITUTIONAL USE ONLY", align="L")

    # RIGHT 40% — large sentiment box
    rx = left_w + 2
    rw = W - rx - 2
    pdf.set_fill_color(*sent_color)
    pdf.rect(rx, 2, rw, strip_h - 4, "F")
    # Sentiment label
    pdf.set_xy(rx, 5)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(*WHITE)
    pdf.cell(rw, 9, sent_label, align="C")
    # Score
    score_txt = f"{sent_score:+.3f}"
    pdf.set_xy(rx, 14)
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(*WHITE)
    pdf.cell(rw, 14, score_txt, align="C")
    # Sub-label
    pdf.set_xy(rx, 30)
    pdf.set_font("Helvetica", "", 7)
    pdf.set_text_color(*WHITE)
    pdf.cell(rw, 5, "COMPOSITE SENTIMENT SCORE", align="C")

    # ── 4 KPI BOXES ──────────────────────────────────────────────────────────
    market = report.market
    alpha  = report.alpha

    n_signals  = len(getattr(alpha,  "signals",           []))
    risk_level = _safe(getattr(market, "risk_level",      "MODERATE")).upper()
    risk_color = _RISK_COLORS.get(risk_level, AMBER)
    dq         = _safe(getattr(report, "data_quality",    "PARTIAL")).upper()
    dq_color   = TEAL if dq == "FULL" else AMBER if dq == "PARTIAL" else CRIMSON

    kpi_y = strip_h + 6
    kpi_h = 26
    kpi_w = (pdf.INNER_W - 9) / 4
    kpis = [
        ("MARKET SENTIMENT",  sent_label,           f"Score: {sent_score:+.3f}",   sent_color),
        ("ALPHA SIGNALS",     _fmt_int(n_signals),  "Active signals",              STEEL),
        ("RISK LEVEL",        risk_level,            "Current assessment",          risk_color),
        ("DATA QUALITY",      dq,                   "Feed status",                 dq_color),
    ]
    for i, (lbl, val, sub, col) in enumerate(kpis):
        bx = LM + i * (kpi_w + 3)
        pdf.kpi_box(bx, kpi_y, kpi_w, kpi_h, lbl, val, sub, col)

    # ── EXECUTIVE HIGHLIGHTS (2 columns) ────────────────────────────────────
    hl_y = kpi_y + kpi_h + 8
    col_w = (pdf.INNER_W - 6) / 2

    # Left: KEY FINDINGS
    pdf.set_xy(LM, hl_y)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*GOLD)
    pdf.cell(col_w, 5, "KEY FINDINGS", align="L")
    pdf.set_y(hl_y + 6)

    top_insights = list(getattr(market, "top_insights", []))[:3]
    for ins in top_insights:
        title  = _safe(getattr(ins, "title",  "—"))[:65]
        action = _safe(getattr(ins, "action", ""))[:12]
        score  = float(getattr(ins, "score",  0.0) or 0.0)
        s_col  = _score_color(score)
        iy = pdf.get_y()
        pdf.set_fill_color(*INK_CARD)
        pdf.rect(LM, iy, col_w, 8, "F")
        pdf.set_fill_color(*s_col)
        pdf.rect(LM, iy, 2, 8, "F")
        pdf.set_xy(LM + 4, iy + 1)
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_text_color(*TEXT_HI)
        pdf.cell(col_w - 28, 4, title, align="L")
        pdf.set_xy(LM + col_w - 22, iy + 1)
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_text_color(*s_col)
        pdf.cell(20, 4, action.upper(), align="R")
        pdf.set_y(iy + 9)

    # Right: MARKET BRIEF
    rx2 = LM + col_w + 6
    pdf.set_xy(rx2, hl_y)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*GOLD)
    pdf.cell(col_w, 5, "MARKET BRIEF", align="L")

    exec_summary = _safe(getattr(report.ai, "executive_summary", ""))
    paras = _split_paragraphs(exec_summary)
    first_para = paras[0] if paras else "Market intelligence is being aggregated."
    pdf.set_xy(rx2, hl_y + 6)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*TEXT_MID)
    pdf.multi_cell(col_w, 4.5, first_para[:500], align="J")

    # ── BOTTOM STRIP ─────────────────────────────────────────────────────────
    pdf.set_fill_color(*INK_SURFACE)
    pdf.rect(0, H - 14, W, 14, "F")
    pdf.set_draw_color(*GOLD)
    pdf.set_line_width(0.4)
    pdf.line(0, H - 14, W, H - 14)
    pdf.set_xy(LM, H - 10)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(*TEXT_LO)
    pdf.cell(W - 2 * LM, 5,
             "GENERATED BY SHIP TRACKER INTELLIGENCE PLATFORM  "
             "|  NOT INVESTMENT ADVICE  |  SEE DISCLAIMER",
             align="C")


def _page_executive_summary(pdf: InstitutionalReportPDF, report) -> None:
    """Page 2: Executive Summary."""
    pdf.add_page()
    pdf.section_header("02  |  Executive Summary",
                       "Composite market view — synthesised from all data sources")

    ai     = report.ai
    alpha  = report.alpha
    market = report.market

    # Portfolio snapshot KPIs
    portfolio = getattr(alpha, "portfolio", {}) or {}
    exp_ret = float(portfolio.get("expected_return", 0.0) or 0.0)
    sharpe  = float(portfolio.get("sharpe", 0.0) or 0.0)
    vol     = float(portfolio.get("portfolio_vol", 0.0) or 0.0)
    max_dd  = float(portfolio.get("max_dd_estimate", 0.0) or 0.0)

    kpi_w = (pdf.INNER_W - 9) / 4
    kpi_h = 22
    kpi_y = pdf.get_y()
    kpis = [
        ("Expected Return",  f"{exp_ret:+.1f}%",  "Portfolio avg",    _change_color(exp_ret)),
        ("Sharpe Ratio",     f"{sharpe:.2f}",       "Risk-adjusted",    STEEL),
        ("Portfolio Vol",    f"{vol:.1f}%",         "Annual est.",      AMBER),
        ("Max Drawdown Est", f"{max_dd:+.1f}%",    "Downside case",    CRIMSON),
    ]
    for i, (lbl, val, sub, col) in enumerate(kpis):
        pdf.kpi_box(pdf.L_MARG + i * (kpi_w + 3), kpi_y, kpi_w, kpi_h, lbl, val, sub, col)
    pdf.set_y(kpi_y + kpi_h + 6)
    pdf.rule(GOLD, 0.3)

    # Full executive summary prose
    exec_summary = _safe(getattr(ai, "executive_summary", ""))
    paras = _split_paragraphs(exec_summary)
    for para in paras[:3]:
        pdf.prose(para)
        pdf.ln(1)

    pdf.rule(INK_BORDER)

    # Two columns: Investment Thesis | Risk Factors
    top_insights = list(getattr(market, "top_insights", []))[:3]
    risk_narrative = _safe(getattr(ai, "risk_narrative", ""))

    col_w = (pdf.INNER_W - 5) / 2
    start_y = pdf.get_y()

    # Left: INVESTMENT THESIS
    pdf.set_xy(pdf.L_MARG, start_y)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*GOLD)
    pdf.cell(col_w, 5, "INVESTMENT THESIS", align="L")
    pdf.set_y(start_y + 6)

    for i, ins in enumerate(top_insights, 1):
        title  = _safe(getattr(ins, "title",  "—"))
        detail = _safe(getattr(ins, "detail", ""))[:100]
        score  = float(getattr(ins, "score",  0.0) or 0.0)
        iy = pdf.get_y()
        if pdf.will_page_break(14):
            break
        pdf.set_fill_color(*INK_CARD)
        pdf.rect(pdf.L_MARG, iy, col_w, 14, "F")
        pdf.set_fill_color(*_score_color(score))
        pdf.rect(pdf.L_MARG, iy, 2, 14, "F")
        # Number
        pdf.set_xy(pdf.L_MARG + 4, iy + 1.5)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*GOLD)
        pdf.cell(6, 6, str(i), align="C")
        # Title
        pdf.set_xy(pdf.L_MARG + 12, iy + 1.5)
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_text_color(*TEXT_HI)
        pdf.cell(col_w - 14, 4.5, str(title)[:55], align="L")
        # Detail
        pdf.set_xy(pdf.L_MARG + 12, iy + 7)
        pdf.set_font("Helvetica", "", 6.5)
        pdf.set_text_color(*TEXT_MID)
        pdf.cell(col_w - 14, 4, detail[:90], align="L")
        pdf.set_y(iy + 15)

    left_end = pdf.get_y()

    # Right: RISK FACTORS
    pdf.set_xy(pdf.L_MARG + col_w + 5, start_y)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*CRIMSON)
    pdf.cell(col_w, 5, "RISK FACTORS", align="L")
    pdf.set_xy(pdf.L_MARG + col_w + 5, start_y + 6)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*TEXT_MID)
    risk_paras = _split_paragraphs(risk_narrative)
    for rp in risk_paras[:2]:
        pdf.set_x(pdf.L_MARG + col_w + 5)
        pdf.multi_cell(col_w, 4.5, rp[:500], align="J")
        pdf.set_x(pdf.L_MARG + col_w + 5)
        pdf.ln(2)

    right_end = pdf.get_y()
    pdf.set_y(max(left_end, right_end) + 3)


def _page_sentiment(pdf: InstitutionalReportPDF, report) -> None:
    """Page 3: Market Sentiment Analysis."""
    pdf.add_page()
    pdf.section_header("03  |  Market Sentiment Analysis",
                       "Multi-source composite reading across news, freight, macro, and alpha")

    sent          = report.sentiment
    overall_score = float(getattr(sent, "overall_score", 0.0) or 0.0)
    overall_label = _safe(getattr(sent, "overall_label", "NEUTRAL")).upper()
    bullish_count = int(getattr(sent, "bullish_count",  0) or 0)
    bearish_count = int(getattr(sent, "bearish_count",  0) or 0)
    neutral_count = int(getattr(sent, "neutral_count",  0) or 0)
    top_keywords  = list(getattr(sent, "top_keywords",  []))
    trending      = list(getattr(sent, "trending_topics", []))
    news_score    = float(getattr(sent, "news_score",   0.0) or 0.0)
    freight_score = float(getattr(sent, "freight_score", 0.0) or 0.0)
    macro_score   = float(getattr(sent, "macro_score",  0.0) or 0.0)
    alpha_score   = float(getattr(sent, "alpha_score",  0.0) or 0.0)

    total = bullish_count + bearish_count + neutral_count or 1

    # ── TOP ROW: 3 mini stat boxes ────────────────────────────────────────────
    ms_y = pdf.get_y()
    ms_h = 22
    ms_w = (pdf.INNER_W - 6) / 3
    pdf.mini_stat(pdf.L_MARG,             ms_y, ms_w, ms_h,
                  f"Bullish  ({bullish_count / total * 100:.0f}%)",
                  str(bullish_count), TEAL)
    pdf.mini_stat(pdf.L_MARG + ms_w + 3,  ms_y, ms_w, ms_h,
                  f"Bearish  ({bearish_count / total * 100:.0f}%)",
                  str(bearish_count), CRIMSON)
    pdf.mini_stat(pdf.L_MARG + 2*(ms_w+3), ms_y, ms_w, ms_h,
                  f"Neutral  ({neutral_count / total * 100:.0f}%)",
                  str(neutral_count), TEXT_LO)
    pdf.set_y(ms_y + ms_h + 5)

    # ── Chart (if available) ──────────────────────────────────────────────────
    if _CHARTS_OK:
        try:
            chart_bytes = pdf_charts.sentiment_gauge_chart(report)
            if chart_bytes:
                pdf.embed_chart(chart_bytes, w=80, x=pdf.L_MARG)
        except Exception:
            pass

    # ── Sentiment component table ─────────────────────────────────────────────
    pdf.sub_heading("Sentiment Components")
    comp_rows = [
        ["News Sentiment",    f"{news_score:+.3f}",    _labelize(news_score),    ""],
        ["Freight Momentum",  f"{freight_score:+.3f}", _labelize(freight_score), ""],
        ["Macro Signal",      f"{macro_score:+.3f}",   _labelize(macro_score),   ""],
        ["Alpha Conviction",  f"{alpha_score:+.3f}",   _labelize(alpha_score),   ""],
        ["COMPOSITE",         f"{overall_score:+.3f}", overall_label,            ""],
    ]
    row_colors = [
        _score_color_abs(news_score),
        _score_color_abs(freight_score),
        _score_color_abs(macro_score),
        _score_color_abs(alpha_score),
        _sentiment_color(overall_label),
    ]
    pdf.data_table(
        headers=["Component", "Score", "Reading", ""],
        rows=comp_rows,
        col_widths=[70, 35, 55, 25.9],
        row_colors=row_colors,
    )
    pdf.ln(4)

    # ── Trending keywords — pill layout ──────────────────────────────────────
    if top_keywords:
        pdf.sub_heading("Trending Keywords & Entities")
        kw_x = pdf.L_MARG
        kw_y = pdf.get_y()
        pill_h = 6
        pill_gap = 2
        for kw in top_keywords[:16]:
            kw_str = str(kw)[:18]
            pill_w = len(kw_str) * 1.9 + 6
            if kw_x + pill_w > pdf.PAGE_W - pdf.R_MARG:
                kw_x = pdf.L_MARG
                kw_y += pill_h + pill_gap
            pdf.set_fill_color(*INK_BORDER)
            pdf.rect(kw_x, kw_y, pill_w, pill_h, "F")
            pdf.set_xy(kw_x + 3, kw_y + 1)
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(*GOLD)
            pdf.cell(pill_w - 6, 4, kw_str, align="L")
            kw_x += pill_w + pill_gap
        pdf.set_y(kw_y + pill_h + 4)

    # ── Trending topics table ─────────────────────────────────────────────────
    if trending:
        pdf.sub_heading("Trending Topics")
        topic_rows = []
        for t in trending[:8]:
            if isinstance(t, dict):
                topic   = _safe(t.get("topic",     "—"))[:38]
                count   = _safe(t.get("count",     "—"))
                avg_s   = t.get("sentiment", 0.0)
                reading = _safe(t.get("color",     _labelize(float(avg_s or 0))))
            else:
                topic   = _safe(getattr(t, "topic",     "—"))[:38]
                count   = _safe(getattr(t, "count",     "—"))
                avg_s   = getattr(t, "sentiment", 0.0)
                reading = _safe(getattr(t, "color",     _labelize(float(avg_s or 0))))
            topic_rows.append([topic, count, f"{float(avg_s or 0):+.3f}", reading])
        pdf.data_table(
            headers=["Topic", "Count", "Avg Sentiment", "Reading"],
            rows=topic_rows,
            col_widths=[80, 25, 40, 40.9],
        )
        pdf.ln(4)

    # ── Top 5 headlines ───────────────────────────────────────────────────────
    news_items = list(getattr(report, "news_items", []))[:5]
    if news_items:
        pdf.sub_heading("Top Headlines")
        headline_rows = []
        for i, art in enumerate(news_items, 1):
            headline = _safe(getattr(art, "title",
                             getattr(art, "headline", "—")))[:60]
            source   = _safe(getattr(art, "source",   "—"))[:18]
            art_sent = _safe(getattr(art, "sentiment_label",
                             getattr(art, "sentiment", "—")))[:12]
            date_str = _safe(getattr(art, "published_date",
                             getattr(art, "date", "—")))[:12]
            headline_rows.append([str(i), headline, source, art_sent, date_str])
        pdf.data_table(
            headers=["#", "Headline", "Source", "Sentiment", "Date"],
            rows=headline_rows,
            col_widths=[8, 106, 32, 22, 17.9],
        )
        pdf.ln(4)

    # ── Narrative ─────────────────────────────────────────────────────────────
    narrative = _safe(getattr(report.ai, "sentiment_narrative", ""))
    if narrative:
        pdf.sub_heading("Analyst Commentary")
        for para in _split_paragraphs(narrative)[:2]:
            pdf.prose(para)
            pdf.ln(1)


def _page_alpha(pdf: InstitutionalReportPDF, report) -> None:
    """Page 4: Alpha Signal Intelligence."""
    pdf.add_page()
    pdf.section_header("04  |  Alpha Signal Intelligence",
                       "Quantitative signals — entry / target / stop / conviction")

    alpha     = report.alpha
    portfolio = getattr(alpha, "portfolio", {}) or {}
    signals   = list(getattr(alpha, "signals",   []))
    top_long  = list(getattr(alpha, "top_long",  []))
    top_short = list(getattr(alpha, "top_short", []))

    # Portfolio metrics
    exp_ret = float(portfolio.get("expected_return", 0.0) or 0.0)
    sharpe  = float(portfolio.get("sharpe", 0.0) or 0.0)
    vol     = float(portfolio.get("portfolio_vol", 0.0) or 0.0)
    max_dd  = float(portfolio.get("max_dd_estimate", 0.0) or 0.0)

    kpi_w = (pdf.INNER_W - 9) / 4
    kpi_h = 22
    kpi_y = pdf.get_y()
    pdf.kpi_box(pdf.L_MARG,                  kpi_y, kpi_w, kpi_h,
                "Expected Return",  f"{exp_ret:+.1f}%",  "Portfolio avg",
                _change_color(exp_ret))
    pdf.kpi_box(pdf.L_MARG + (kpi_w + 3),    kpi_y, kpi_w, kpi_h,
                "Sharpe Ratio",     f"{sharpe:.2f}",      "Risk-adjusted", STEEL)
    pdf.kpi_box(pdf.L_MARG + 2*(kpi_w + 3),  kpi_y, kpi_w, kpi_h,
                "Portfolio Vol",    f"{vol:.1f}%",         "Annual est.",   AMBER)
    pdf.kpi_box(pdf.L_MARG + 3*(kpi_w + 3),  kpi_y, kpi_w, kpi_h,
                "Max Drawdown",     f"{max_dd:+.1f}%",    "Downside case", CRIMSON)
    pdf.set_y(kpi_y + kpi_h + 5)
    pdf.rule(GOLD, 0.3)

    # Full signal scorecard
    all_sigs = top_long[:6] + top_short[:4]
    if all_sigs:
        pdf.sub_heading("Signal Scorecard")
        sig_rows   = []
        sig_colors = []
        for sig in all_sigs:
            direction  = _safe(getattr(sig, "direction",    "LONG")).upper()
            name       = _safe(getattr(sig, "signal_name",  "—"))[:26]
            ticker     = _safe(getattr(sig, "ticker",        "—"))[:8]
            sig_type   = _safe(getattr(sig, "signal_type",  "—"))[:14]
            strength   = _safe(getattr(sig, "strength",     "—"))[:8]
            conviction = _safe(getattr(sig, "conviction",   "—")).upper()[:8]
            entry      = _fmt_price(getattr(sig, "entry_price",  0.0))
            target     = _fmt_price(getattr(sig, "target_price", 0.0))
            stop       = _fmt_price(getattr(sig, "stop_loss",    0.0))
            ret_pct    = f"{getattr(sig, 'expected_return_pct', 0.0) or 0.0:+.1f}%"
            horizon    = _safe(getattr(sig, "time_horizon", "—"))[:8]
            dir_arrow  = "▲" if direction == "LONG" else "▼"
            sig_rows.append([f"{dir_arrow} {name}", ticker, sig_type, direction,
                              strength, conviction, entry, target, stop, ret_pct, horizon])
            sig_colors.append(TEAL if direction == "LONG" else CRIMSON)

        pdf.data_table(
            headers=["SIGNAL", "TICKER", "TYPE", "DIR", "STR", "CONV",
                     "ENTRY", "TARGET", "STOP", "RETURN", "HORIZON"],
            rows=sig_rows,
            col_widths=[44, 14, 22, 12, 12, 14, 18, 18, 17, 16, 14.9],
            row_colors=sig_colors,
        )
        pdf.ln(4)

    # Chart embed
    if _CHARTS_OK:
        try:
            chart_bytes = pdf_charts.conviction_breakdown_chart(report)
            if chart_bytes:
                pdf.embed_chart(chart_bytes, w=180)
        except Exception:
            pass

    # Narrative
    opp = _safe(getattr(report.ai, "opportunity_narrative", ""))
    if opp:
        pdf.sub_heading("Opportunity Narrative")
        for para in _split_paragraphs(opp)[:3]:
            pdf.prose(para)
            pdf.ln(1)

    if not all_sigs:
        pdf.prose(
            "No alpha signals generated in this cycle. This may indicate insufficient "
            "price momentum, conflicting macro data, or incomplete feeds. Monitor the "
            "next refresh cycle for breakout conditions.",
            color=TEXT_LO,
        )


def _page_market_intelligence(pdf: InstitutionalReportPDF, report) -> None:
    """Page 5: Market Intelligence & Insights."""
    pdf.add_page()
    pdf.section_header("05  |  Market Intelligence & Insights",
                       "Top-ranked insights, ports, and trade routes")

    market = report.market
    risk_level = _safe(getattr(market, "risk_level", "MODERATE")).upper()
    risk_color = _RISK_COLORS.get(risk_level, AMBER)
    active_opps = int(getattr(market, "active_opportunities",  0) or 0)
    high_conv   = int(getattr(market, "high_conviction_count", 0) or 0)

    # ── Risk level badge ──────────────────────────────────────────────────────
    ry = pdf.get_y()
    pdf.set_fill_color(*risk_color)
    pdf.rect(pdf.L_MARG, ry, 68, 12, "F")
    pdf.set_xy(pdf.L_MARG + 3, ry + 2.5)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*WHITE)
    pdf.cell(62, 7, f"RISK LEVEL: {risk_level}", align="C")

    pdf.set_xy(pdf.L_MARG + 74, ry + 1)
    pdf.set_font("Helvetica", "", 7)
    pdf.set_text_color(*TEXT_LO)
    pdf.cell(60, 4, f"Active Opportunities: {active_opps}", align="L")
    pdf.set_xy(pdf.L_MARG + 74, ry + 6)
    pdf.cell(60, 4, f"High Conviction Insights: {high_conv}", align="L")
    pdf.set_y(ry + 16)

    # ── Top 5 insights as cards ────────────────────────────────────────────────
    top_insights = list(getattr(market, "top_insights", []))[:5]
    if top_insights:
        pdf.sub_heading("Top Market Insights")
        for i, ins in enumerate(top_insights, 1):
            title    = _safe(getattr(ins, "title",    "—"))
            detail   = _safe(getattr(ins, "detail",   ""))[:110]
            score    = float(getattr(ins, "score",    0.0) or 0.0)
            action   = _safe(getattr(ins, "action",   "Monitor"))
            category = _safe(getattr(ins, "category", "General"))
            # Affected assets
            ports_l  = getattr(ins, "ports",  None) or getattr(ins, "affected_ports",  [])
            routes_l = getattr(ins, "routes", None) or getattr(ins, "affected_routes", [])
            stocks_l = getattr(ins, "stocks", None) or getattr(ins, "affected_stocks", [])
            ports_s  = ", ".join(str(p) for p in (ports_l  or [])[:3])
            routes_s = ", ".join(str(r) for r in (routes_l or [])[:3])
            stocks_s = ", ".join(str(s) for s in (stocks_l or [])[:4])
            pdf.insight_card(i, title, detail, score, action, category,
                             ports_s, routes_s, stocks_s)

    pdf.ln(2)

    # ── Top Ports table ───────────────────────────────────────────────────────
    top_ports = list(getattr(market, "top_ports", []))[:5]
    if top_ports:
        pdf.sub_heading("Top Ports by Demand Score")
        port_rows = []
        for i, pr in enumerate(top_ports, 1):
            if isinstance(pr, dict):
                name   = _safe(pr.get("port_name") or pr.get("port") or pr.get("name") or "—")[:28]
                region = _safe(pr.get("region",  "—"))[:18]
                score  = float(pr.get("demand_score") or pr.get("score", 0) or 0)
                cong   = _safe(pr.get("congestion", pr.get("trend", "—")))[:12]
                status = _safe(pr.get("status") or pr.get("demand_label", "—"))[:14]
            else:
                name   = _safe(getattr(pr, "port_name", getattr(pr, "port_id", "—")))[:28]
                region = _safe(getattr(pr, "region",  "—"))[:18]
                score  = float(getattr(pr, "demand_score", 0) or 0)
                cong   = _safe(getattr(pr, "trend",  "—"))[:12]
                status = _safe(getattr(pr, "demand_label", "—"))[:14]
            port_rows.append([str(i), name, region, f"{score:.2f}", cong, status])
        pdf.data_table(
            headers=["#", "Port", "Region", "Demand Score", "Congestion", "Status"],
            rows=port_rows,
            col_widths=[8, 55, 40, 24, 30, 28.9],
        )
        pdf.ln(4)

    # ── Top Routes table ──────────────────────────────────────────────────────
    top_routes = list(getattr(market, "top_routes", []))[:5]
    if top_routes:
        pdf.sub_heading("Top Trade Routes")
        route_rows = []
        for i, rr in enumerate(top_routes, 1):
            if isinstance(rr, dict):
                name  = _safe(rr.get("route") or rr.get("lane") or rr.get("name", "—"))[:42]
                score = float(rr.get("score", 0) or 0)
                rate  = f"${rr.get('rate', rr.get('current_rate', 0)):,.0f}"
                chg   = f"{float(rr.get('change_pct', 0) or 0):+.1f}%"
                opp   = _safe(rr.get("label", rr.get("trend", "—")))[:14]
            else:
                name  = _safe(getattr(rr, "route", getattr(rr, "name", "—")))[:42]
                score = float(getattr(rr, "score", 0) or 0)
                rate  = f"${getattr(rr, 'rate', 0):,.0f}"
                chg   = f"{float(getattr(rr, 'change_pct', 0) or 0):+.1f}%"
                opp   = _safe(getattr(rr, "label", "—"))[:14]
            route_rows.append([str(i), name, f"{score:.2f}", rate, chg, opp])
        pdf.data_table(
            headers=["#", "Route", "Score", "Rate ($/FEU)", "30D Chg", "Opportunity"],
            rows=route_rows,
            col_widths=[8, 78, 18, 28, 20, 33.9],
        )


def _page_freight(pdf: InstitutionalReportPDF, report) -> None:
    """Page 6: Freight Rate Analysis."""
    pdf.add_page()
    pdf.section_header("06  |  Freight Rate Analysis",
                       "FBX composite, route-level rates, momentum, and trend signals")

    freight = report.freight
    avg_chg    = float(getattr(freight, "avg_change_30d_pct", 0.0) or 0.0)
    momentum   = _safe(getattr(freight, "momentum_label",     "Stable"))
    fbx_comp   = float(getattr(freight, "fbx_composite",      0.0) or 0.0)
    biggest    = getattr(freight, "biggest_mover",             {}) or {}
    biggest_id = _safe(biggest.get("route_id", "—"))[:22]
    biggest_chg= float(biggest.get("change_pct", 0.0) or 0.0)

    # Momentum summary KPIs
    kpi_w = (pdf.INNER_W - 9) / 4
    kpi_h = 22
    kpi_y = pdf.get_y()
    pdf.kpi_box(pdf.L_MARG,                  kpi_y, kpi_w, kpi_h,
                "FBX Composite",    f"${fbx_comp:,.0f}",  "/FEU average",  STEEL)
    pdf.kpi_box(pdf.L_MARG + (kpi_w + 3),    kpi_y, kpi_w, kpi_h,
                "Avg 30D Change",   f"{avg_chg:+.1f}%",   "All routes",    _change_color(avg_chg))
    pdf.kpi_box(pdf.L_MARG + 2*(kpi_w + 3),  kpi_y, kpi_w, kpi_h,
                "Rate Momentum",    momentum,              "Direction",     AMBER)
    pdf.kpi_box(pdf.L_MARG + 3*(kpi_w + 3),  kpi_y, kpi_w, kpi_h,
                "Biggest Mover",    f"{biggest_chg:+.1f}%", biggest_id[:16],
                _change_color(biggest_chg))
    pdf.set_y(kpi_y + kpi_h + 5)
    pdf.rule(GOLD, 0.3)

    # Full freight routes table
    freight_routes = list(getattr(freight, "routes", []))
    if freight_routes:
        pdf.sub_heading("Freight Rate Snapshot — All Monitored Routes")
        frt_rows   = []
        frt_colors = []
        for rt in freight_routes[:12]:
            if isinstance(rt, dict):
                route_id = _safe(rt.get("route_id", rt.get("route", "—")))[:38]
                rate     = f"${rt.get('rate', 0):,.0f}"
                chg_30d  = f"${float(rt.get('change_30d', 0) or 0):+,.0f}"
                chg_pct  = f"{float(rt.get('change_pct', 0) or 0):+.1f}%"
                trend    = _safe(rt.get("label", rt.get("trend", "—")))[:12]
            else:
                route_id = _safe(getattr(rt, "route_id", getattr(rt, "route", "—")))[:38]
                rate     = f"${getattr(rt, 'rate', 0):,.0f}"
                chg_30d  = f"${float(getattr(rt, 'change_30d', 0) or 0):+,.0f}"
                chg_pct  = f"{float(getattr(rt, 'change_pct', 0) or 0):+.1f}%"
                trend    = _safe(getattr(rt, "label", "—"))[:12]
            frt_rows.append([route_id, rate, chg_30d, chg_pct, trend])
            frt_colors.append(_change_color(float(
                (rt.get("change_pct", 0) if isinstance(rt, dict)
                 else getattr(rt, "change_pct", 0)) or 0)))
        pdf.data_table(
            headers=["Route", "Rate ($/FEU)", "30D Chg ($)", "30D Chg (%)", "Trend"],
            rows=frt_rows,
            col_widths=[72, 30, 30, 30, 23.9],
            row_colors=frt_colors,
        )
        pdf.ln(4)

    # Chart embed
    if _CHARTS_OK:
        try:
            chart_bytes = pdf_charts.freight_rates_chart(report)
            if chart_bytes:
                pdf.embed_chart(chart_bytes, w=185)
        except Exception:
            pass


def _page_macro(pdf: InstitutionalReportPDF, report) -> None:
    """Page 7: Macro Environment."""
    pdf.add_page()
    pdf.section_header("07  |  Macro Environment",
                       "BDI, WTI, treasury yields, PMI proxy, and supply chain stress index")

    macro = report.macro

    # Chart first (if available)
    if _CHARTS_OK:
        try:
            chart_bytes = pdf_charts.macro_snapshot_chart(report)
            if chart_bytes:
                pdf.embed_chart(chart_bytes, w=185)
        except Exception:
            pass

    # Extract macro values
    bdi      = float(getattr(macro, "bdi",               0.0) or 0.0)
    bdi_chg  = float(getattr(macro, "bdi_change_30d_pct", 0.0) or 0.0)
    wti      = float(getattr(macro, "wti",               0.0) or 0.0)
    wti_chg  = float(getattr(macro, "wti_change_30d_pct", 0.0) or 0.0)
    tsy      = float(getattr(macro, "treasury_10y",      0.0) or 0.0)
    dxy      = float(getattr(macro, "dxy_proxy",         0.0) or 0.0)
    pmi      = float(getattr(macro, "pmi_proxy",         0.0) or 0.0)
    stress   = _safe(getattr(macro, "supply_chain_stress", "MODERATE")).upper()
    stress_col = _RISK_COLORS.get(stress, AMBER)

    # 2-column, 3-metric grid
    pdf.sub_heading("Macro Indicators")
    col_w = (pdf.INNER_W - 5) / 2
    metrics_left = [
        ("Baltic Dry Index (BDI)",   f"{int(bdi):,}" if bdi else "N/A",
         f"30D: {bdi_chg:+.1f}%",   _change_color(bdi_chg)),
        ("WTI Crude Oil",            f"${wti:.2f}" if wti else "N/A",
         f"30D: {wti_chg:+.1f}%",   _change_color(wti_chg)),
        ("10Y US Treasury",          f"{tsy:.2f}%" if tsy else "N/A",
         "US Yield",                 STEEL),
    ]
    metrics_right = [
        ("Supply Chain Stress",  stress,
         "BDI + WTI + Rates",   stress_col),
        ("PMI Proxy",            f"{pmi:.1f}" if pmi else "N/A",
         "Industrial production", PURPLE),
        ("DXY Proxy (USD/CNY)",  f"{dxy:.4f}" if dxy else "N/A",
         "Currency signal",      AMBER),
    ]
    box_h = 22
    start_y = pdf.get_y()
    for i, (lbl, val, sub, col) in enumerate(metrics_left):
        pdf.kpi_box(pdf.L_MARG, start_y + i * (box_h + 3),
                    col_w, box_h, lbl, val, sub, col)
    for i, (lbl, val, sub, col) in enumerate(metrics_right):
        pdf.kpi_box(pdf.L_MARG + col_w + 5, start_y + i * (box_h + 3),
                    col_w, box_h, lbl, val, sub, col)
    pdf.set_y(start_y + 3 * (box_h + 3) + 4)

    # Risk narrative
    risk_narrative = _safe(getattr(report.ai, "risk_narrative", ""))
    if risk_narrative:
        pdf.sub_heading("Risk Narrative")
        for para in _split_paragraphs(risk_narrative)[:3]:
            pdf.prose(para)
            pdf.ln(1)


def _page_stocks(pdf: InstitutionalReportPDF, report) -> None:
    """Page 8: Shipping Equity Analysis."""
    pdf.add_page()
    pdf.section_header("08  |  Shipping Equity Analysis",
                       "Equity performance, signal coverage, and conviction rankings")

    stocks_obj   = report.stocks
    tickers      = list(getattr(stocks_obj, "tickers",           []))
    prices       = dict(getattr(stocks_obj, "prices",            {}))
    changes_30d  = dict(getattr(stocks_obj, "changes_30d",       {}))
    changes_90d  = dict(getattr(stocks_obj, "changes_90d",       {}) if
                        hasattr(stocks_obj, "changes_90d") else {})
    signals_by_t = dict(getattr(stocks_obj, "signals_by_ticker", {}))
    top_pick     = _safe(getattr(stocks_obj, "top_pick",           "—"))
    top_rat      = _safe(getattr(stocks_obj, "top_pick_rationale", ""))

    # Stock comparison table
    if tickers:
        pdf.sub_heading("Shipping Equity Comparison")
        stock_rows  = []
        stock_colors= []
        for t in tickers:
            price  = prices.get(t, 0.0) or 0.0
            chg30  = changes_30d.get(t, 0.0) or 0.0
            chg90  = changes_90d.get(t, 0.0) or 0.0
            t_sigs = signals_by_t.get(t, []) or []
            n_sigs = len(t_sigs)
            top_sig_name = _safe(getattr(t_sigs[0], "signal_name", "—")) if t_sigs else "—"
            conv  = _safe(getattr(t_sigs[0], "conviction", "—")).upper() if t_sigs else "—"
            conv_col = _CONVICTION_COLORS.get(conv, TEXT_LO)
            stock_rows.append([
                t,
                _fmt_price(price),
                f"{chg30:+.1f}%",
                f"{chg90:+.1f}%",
                str(n_sigs),
                top_sig_name[:30],
                conv,
            ])
            stock_colors.append(_change_color(chg30))
        pdf.data_table(
            headers=["Ticker", "Price", "30D Chg", "90D Chg",
                     "Signals", "Top Signal", "Conviction"],
            rows=stock_rows,
            col_widths=[18, 24, 20, 20, 16, 64, 23.9],
            row_colors=stock_colors,
        )
        pdf.ln(5)

    # TOP PICK box
    if top_pick and top_pick not in ("—", "N/A"):
        pick_price = prices.get(top_pick, 0.0) or 0.0
        pick_chg   = changes_30d.get(top_pick, 0.0) or 0.0

        if pdf.will_page_break(20):
            pdf.add_page()
        py = pdf.get_y()
        pdf.set_fill_color(*INK_CARD)
        pdf.rect(pdf.L_MARG, py, pdf.INNER_W, 20, "F")
        pdf.set_fill_color(*TEAL)
        pdf.rect(pdf.L_MARG, py, 3.5, 20, "F")
        # Label
        pdf.set_xy(pdf.L_MARG + 7, py + 2)
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_text_color(*GOLD)
        pdf.cell(30, 4, "TOP PICK", align="L")
        # Ticker
        pdf.set_xy(pdf.L_MARG + 7, py + 7)
        pdf.set_font("Helvetica", "B", 16)
        pdf.set_text_color(*TEXT_HI)
        pdf.cell(28, 9, top_pick, align="L")
        # Price / change
        pdf.set_xy(pdf.L_MARG + 42, py + 7)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*_change_color(pick_chg))
        pdf.cell(25, 8, f"{pick_chg:+.1f}%", align="L")
        pdf.set_xy(pdf.L_MARG + 70, py + 7)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*TEXT_MID)
        pdf.cell(22, 8, _fmt_price(pick_price), align="L")
        # Rationale
        pdf.set_xy(pdf.L_MARG + 7, py + 15)
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(*TEXT_MID)
        pdf.cell(pdf.INNER_W - 14, 4, top_rat[:140], align="L")
        pdf.set_y(py + 24)

    # Chart
    if _CHARTS_OK:
        try:
            chart_bytes = pdf_charts.stock_performance_chart(report)
            if chart_bytes:
                pdf.embed_chart(chart_bytes, w=185)
        except Exception:
            pass

    # Signal-by-ticker breakdown
    if signals_by_t:
        pdf.sub_heading("Signal Breakdown by Ticker")
        for ticker, sigs in list(signals_by_t.items())[:6]:
            if not sigs:
                continue
            if pdf.will_page_break(10):
                pdf.add_page()
            by = pdf.get_y()
            pdf.set_xy(pdf.L_MARG, by)
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(*GOLD)
            pdf.cell(20, 5, ticker, align="L")
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(*TEXT_MID)
            sig_strs = []
            for sg in sigs[:4]:
                nm  = _safe(getattr(sg, "signal_name", "—"))[:18]
                dir_= _safe(getattr(sg, "direction",   "—"))[:4]
                ret_= float(getattr(sg, "expected_return_pct", 0.0) or 0.0)
                sig_strs.append(f"{nm} [{dir_}] {ret_:+.1f}%")
            pdf.set_xy(pdf.L_MARG + 22, by)
            pdf.cell(pdf.INNER_W - 22, 5, "  |  ".join(sig_strs)[:140], align="L")
            pdf.ln(6)


def _page_recommendations(pdf: InstitutionalReportPDF, report) -> None:
    """Page 9: AI Recommendations."""
    pdf.add_page()
    pdf.section_header("09  |  AI Recommendations",
                       "Rule-based signal synthesis — ranked by conviction and expected return")

    # Warning bar
    wy = pdf.get_y()
    pdf.set_fill_color(*AMBER)
    pdf.rect(pdf.L_MARG, wy, pdf.INNER_W, 7, "F")
    pdf.set_xy(pdf.L_MARG + 3, wy + 1.5)
    pdf.set_font("Helvetica", "B", 7.5)
    pdf.set_text_color(*BLACK)
    pdf.cell(pdf.INNER_W - 6, 4,
             "AI-GENERATED RECOMMENDATIONS — NOT INVESTMENT ADVICE — SEE DISCLAIMER",
             align="C")
    pdf.set_y(wy + 9)

    top_recommendations = list(getattr(report.ai, "top_recommendations", []))
    for rec in top_recommendations[:5]:
        try:
            pdf.rec_card(rec)
        except Exception:
            pass

    if not top_recommendations:
        pdf.prose("No structured recommendations generated in this cycle.", color=TEXT_LO)

    # 30-Day Outlook box
    outlook = _safe(getattr(report.ai, "outlook_30d", ""))
    if outlook:
        if pdf.will_page_break(30):
            pdf.add_page()
        oy = pdf.get_y() + 3
        pdf.set_fill_color(*INK_SURFACE)
        pdf.rect(pdf.L_MARG, oy, pdf.INNER_W, 4, "F")
        # GOLD top border
        pdf.set_fill_color(*GOLD)
        pdf.rect(pdf.L_MARG, oy, pdf.INNER_W, 1.2, "F")
        # Label
        pdf.set_xy(pdf.L_MARG + 3, oy + 1.5)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*GOLD)
        pdf.cell(40, 4, "30-DAY OUTLOOK", align="L")
        pdf.set_y(oy + 7)

        for para in _split_paragraphs(outlook)[:2]:
            pdf.prose(para)
            pdf.ln(1)


def _page_disclaimer(pdf: InstitutionalReportPDF, report) -> None:
    """Page 10: Disclaimer & Appendix."""
    pdf.add_page()
    pdf.section_header("10  |  Disclaimer & Appendix",
                       "Data sources, methodology, and regulatory disclaimer")

    ai = report.ai

    # Full signal scorecard (all signals, condensed)
    alpha   = report.alpha
    signals = list(getattr(alpha, "signals", []))
    if signals:
        pdf.sub_heading("Complete Signal Scorecard")
        all_rows   = []
        all_colors = []
        for sig in signals[:20]:
            direction  = _safe(getattr(sig, "direction",    "LONG")).upper()
            name       = _safe(getattr(sig, "signal_name",  "—"))[:24]
            ticker     = _safe(getattr(sig, "ticker",        "—"))[:7]
            sig_type   = _safe(getattr(sig, "signal_type",  "—"))[:14]
            conviction = _safe(getattr(sig, "conviction",   "—")).upper()[:8]
            entry      = _fmt_price(getattr(sig, "entry_price",  0.0))
            target     = _fmt_price(getattr(sig, "target_price", 0.0))
            ret_pct    = f"{getattr(sig, 'expected_return_pct', 0.0) or 0.0:+.1f}%"
            horizon    = _safe(getattr(sig, "time_horizon", "—"))[:8]
            arr        = "▲" if direction == "LONG" else "▼"
            all_rows.append([f"{arr}{name}", ticker, sig_type, conviction,
                              entry, target, ret_pct, horizon])
            all_colors.append(TEAL if direction == "LONG" else CRIMSON)
        pdf.data_table(
            headers=["SIGNAL", "TICKER", "TYPE", "CONV", "ENTRY", "TARGET", "RETURN", "HORIZON"],
            rows=all_rows,
            col_widths=[45, 14, 26, 18, 20, 20, 20, 22.9],
            row_colors=all_colors,
        )
        pdf.ln(4)

    # Data sources table
    pdf.sub_heading("Data Sources")
    sources = [
        ["Freight Rates",      "Freightos Baltic Index (FBX), Baltic Exchange", "Daily",  "Active"],
        ["Macroeconomic",      "US Federal Reserve (FRED API)",                 "Daily",  "Active"],
        ["Trade Flows",        "UN Comtrade, World Bank WITS",                  "Monthly","Active"],
        ["Shipping Equities",  "Yahoo Finance (yfinance)",                      "Daily",  "Active"],
        ["Port AIS Data",      "MarineTraffic / AIS aggregation",               "Hourly", "Active"],
        ["Port Registry",      "World Port Index (UKHO)",                       "Static", "Active"],
        ["News & Sentiment",   "RSS feeds, financial newswires, NLP engine",    "Real-time","Active"],
    ]
    pdf.data_table(
        headers=["Source", "Data Type", "Update Frequency", "Status"],
        rows=sources,
        col_widths=[44, 88, 32, 21.9],
    )
    pdf.ln(6)

    # Disclaimer text
    pdf.sub_heading("Investment Disclaimer")
    disclaimer = _safe(getattr(ai, "disclaimer", ""))
    if not disclaimer or disclaimer == "N/A":
        disclaimer = (
            "IMPORTANT DISCLAIMER: This report is generated by the Ship Tracker Intelligence "
            "Platform for informational and research purposes only. It does not constitute "
            "investment advice, a solicitation to buy or sell any security, or a recommendation "
            "of any specific investment strategy. Past performance is not indicative of future "
            "results. All investments involve risk, including the possible loss of principal. "
            "Shipping equities are highly volatile and subject to sector-specific risks including "
            "geopolitical disruption, bunker fuel price shocks, regulatory changes, and global "
            "trade policy shifts. The signals and recommendations contained herein are generated "
            "using rule-based quantitative methods and do not represent the views of any licensed "
            "financial advisor or registered investment advisor. Always consult a qualified "
            "financial professional before making any investment decision. This document is "
            "intended for institutional investors and qualified market participants only and "
            "should not be redistributed without express written consent."
        )
    for para in _split_paragraphs(disclaimer):
        pdf.set_x(pdf.L_MARG)
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(*TEXT_MID)
        pdf.multi_cell(pdf.INNER_W, 4.5, para, align="J")
        pdf.ln(2)

    pdf.ln(4)

    # Platform statement
    pdf.set_x(pdf.L_MARG)
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(*TEXT_LO)
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    pdf.cell(pdf.INNER_W, 4,
             f"Generated: {now_str}  |  "
             f"Report ID: {_safe(report.generated_at)[:30]}  |  "
             f"Data Quality: {_safe(report.data_quality)}",
             align="C")
    pdf.ln(5)
    pdf.set_x(pdf.L_MARG)
    pdf.set_font("Helvetica", "", 6.5)
    pdf.set_text_color(*TEXT_LO)
    pdf.cell(pdf.INNER_W, 4,
             "This report was generated by the Ship Tracker Intelligence Platform. "
             "© Ship Tracker — Confidential. All rights reserved.",
             align="C")


# ═══════════════════════════════════════════════════════════════════════════════
#  INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _labelize(score: float) -> str:
    """Convert a -1..+1 score to a reading label."""
    try:
        v = float(score)
        if v >= 0.3:
            return "BULLISH"
        if v >= 0.05:
            return "MILD BULLISH"
        if v <= -0.3:
            return "BEARISH"
        if v <= -0.05:
            return "MILD BEARISH"
        return "NEUTRAL"
    except (TypeError, ValueError):
        return "—"


def _score_color_abs(score: float) -> tuple:
    """Color based on absolute score value (signed -1..+1)."""
    try:
        v = float(score)
        if v >= 0.1:
            return TEAL
        if v <= -0.1:
            return CRIMSON
        return TEXT_LO
    except (TypeError, ValueError):
        return TEXT_LO


# ═══════════════════════════════════════════════════════════════════════════════
#  PUBLIC EXPORT
# ═══════════════════════════════════════════════════════════════════════════════

def render_investor_report_pdf(report: "InvestorReport") -> bytes:
    """
    Generate a 10-page institutional-grade PDF from an InvestorReport object.

    Parameters
    ----------
    report : InvestorReport
        Populated InvestorReport from processing.investor_report_engine.

    Returns
    -------
    bytes
        Raw PDF bytes — pass directly to st.download_button().

    Raises
    ------
    ImportError
        If fpdf2 is not installed (pip install fpdf2).
    """
    if not _FPDF_OK:
        raise ImportError(
            "fpdf2 is required to generate PDF reports. "
            "Install with: pip install fpdf2"
        )

    def _safe_page(fn, name: str) -> None:
        try:
            fn()
        except Exception as exc:
            try:
                pdf.add_page()
                pdf.set_xy(pdf.L_MARG, 30)
                pdf.set_font("Helvetica", "B", 9)
                pdf.set_text_color(*CRIMSON)
                pdf.cell(0, 8, f"[Error rendering {name}: {str(exc)[:120]}]", align="L")
            except Exception:
                pass

    try:
        pdf = InstitutionalReportPDF()
        pdf._report_date = _safe(getattr(report, "report_date", ""))

        # Fill every page with the dark background before content
        # (fpdf2 doesn't do this automatically — section headers handle it page by page)

        _safe_page(lambda: _page_cover(pdf, report),               "Cover Page")
        _safe_page(lambda: _page_executive_summary(pdf, report),   "Executive Summary")
        _safe_page(lambda: _page_sentiment(pdf, report),           "Sentiment Analysis")
        _safe_page(lambda: _page_alpha(pdf, report),               "Alpha Signals")
        _safe_page(lambda: _page_market_intelligence(pdf, report), "Market Intelligence")
        _safe_page(lambda: _page_freight(pdf, report),             "Freight Rates")
        _safe_page(lambda: _page_macro(pdf, report),               "Macro Environment")
        _safe_page(lambda: _page_stocks(pdf, report),              "Equity Analysis")
        _safe_page(lambda: _page_recommendations(pdf, report),     "Recommendations")
        _safe_page(lambda: _page_disclaimer(pdf, report),          "Disclaimer & Appendix")

        return bytes(pdf.output())

    except Exception as exc:
        # Last-resort fallback — return a minimal error PDF
        try:
            err = FPDF(orientation="P", unit="mm", format="Letter")
            err.add_page()
            err.set_font("Helvetica", "B", 12)
            err.set_text_color(231, 76, 60)
            err.cell(0, 10, "PDF Generation Failed", align="L")
            err.ln(8)
            err.set_font("Helvetica", "", 8)
            err.set_text_color(100, 116, 130)
            err.multi_cell(0, 5,
                           f"An error occurred during PDF generation:\n\n{exc}\n\n"
                           f"{traceback.format_exc()[:800]}", align="L")
            return bytes(err.output())
        except Exception:
            return b""
