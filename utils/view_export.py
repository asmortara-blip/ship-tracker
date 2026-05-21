"""utils/view_export.py — "Export this view" PDF helper.

Lightweight PDF export for any tab. The caller builds a structured
:class:`ViewSnapshot` from whatever's already on screen — headline, paragraphs,
optional tables — and :func:`build_view_pdf` turns it into a PDF byte
string that can feed straight into ``st.download_button``.

Why not Plotly→image capture
----------------------------
Streamlit doesn't ship a screenshot primitive, and ``plotly.to_image`` needs
the heavyweight ``kaleido`` runtime. It's unreliable across environments and
adds startup cost. Tables and headline text are what most users want to take
away anyway — those are exactly what this snapshot captures.

Why not extend utils/investor_report_pdf
----------------------------------------
That's a 2,800-line institutional report builder with a specific schema for
the investor brief. Per-tab export is a different shape — short, generic,
called from any render path. Lives in its own module so its surface stays
focused.

Pure Python — no streamlit imports inside the module itself (so the helper
is unit-testable without a Streamlit runtime). FPDF dependency only.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Snapshot schema
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ViewTable:
    """A simple labeled table to include in the export.

    Headers length should match each row's length. Cells are coerced to ``str``
    so any pre-formatted display string from the tab works as-is.
    """
    title: str
    headers: list[str]
    rows: list[list[str]] = field(default_factory=list)


@dataclass(frozen=True)
class ViewSection:
    """One topical sub-section of the exported view.

    Either ``body`` (paragraph text) or ``tables`` (zero or more tables) — or
    both. Bullets render as a simple "• item" list.
    """
    title: str
    body: str = ""
    bullets: list[str] = field(default_factory=list)
    tables: list[ViewTable] = field(default_factory=list)


@dataclass(frozen=True)
class ViewSnapshot:
    """The complete per-tab snapshot to render."""
    title: str
    subtitle: str = ""
    headline: str = ""                 # Optional accent-bordered top callout
    body: str = ""                     # Optional intro paragraphs (\n\n separated)
    sections: list[ViewSection] = field(default_factory=list)
    footer_note: str = ""              # Source / methodology footer line
    generated_at: str = ""             # ISO 8601 UTC; auto-filled if blank


# ─────────────────────────────────────────────────────────────────────────────
# PDF builder
# ─────────────────────────────────────────────────────────────────────────────

# WSJ-ish palette aligned with the platform's design system.
_INK_HEX = (26, 29, 35)            # main body text
_MUTED_HEX = (108, 103, 96)        # secondary text
_ACCENT_HEX = (53, 114, 176)       # rule + headline border
_RULE_HEX = (232, 230, 225)        # hairline rules

_PAGE_W_MM = 210                   # A4 portrait
_MARGIN_MM = 18


def _utf8_safe(text: str) -> str:
    """FPDF's core fonts only support Latin-1. Replace common smart-quotes /
    arrows / em-dash with safe ASCII or Latin-1 equivalents so the document
    never blows up on Unicode."""
    if not text:
        return ""
    replacements = {
        "—": "--",   # em-dash
        "–": "-",    # en-dash
        "‘": "'",    # left single quote
        "’": "'",    # right single quote
        "“": '"',    # left double quote
        "”": '"',    # right double quote
        "→": "->",   # rightwards arrow
        "←": "<-",   # leftwards arrow
        "↔": "<->",  # left-right arrow
        "…": "...",  # ellipsis
        "•": "*",    # bullet
        "·": "*",    # middle dot
        "≤": "<=",
        "≥": ">=",
        "Δ": "D",    # capital delta
        "σ": "s",    # sigma
        "μ": "u",    # mu
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    # Final safety net — drop anything outside Latin-1.
    try:
        text.encode("latin-1")
        return text
    except UnicodeEncodeError:
        return text.encode("latin-1", errors="replace").decode("latin-1")


def build_view_pdf(snapshot: ViewSnapshot) -> bytes:
    """Render ``snapshot`` to PDF bytes.

    Layout: A4 portrait. Title block at top. Optional accent-bordered
    headline. Optional body paragraphs. Then each section in order with
    title rule, bullets, tables. Footer with generated_at timestamp +
    optional source note on every page.

    Returns the PDF as bytes — feed directly into ``st.download_button``.
    """
    # Lazy import to keep module importable even when fpdf isn't installed
    # (e.g. in a stripped-down test env). The function will still raise then,
    # but the module itself imports clean.
    from fpdf import FPDF

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(_MARGIN_MM, _MARGIN_MM, _MARGIN_MM)
    pdf.set_creator("Ship Tracker / view_export")
    pdf.set_title(snapshot.title or "Ship Tracker view")

    generated = snapshot.generated_at or datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC"
    )
    pdf.alias_nb_pages()
    pdf.add_page()

    # ── Title block ──────────────────────────────────────────────────────────
    pdf.set_text_color(*_INK_HEX)
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 9, _utf8_safe(snapshot.title), ln=1)
    if snapshot.subtitle:
        pdf.set_text_color(*_MUTED_HEX)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_x(_MARGIN_MM)
        pdf.multi_cell(0, 5, _utf8_safe(snapshot.subtitle))
        pdf.ln(1)

    # Underline rule
    pdf.set_draw_color(*_RULE_HEX)
    pdf.set_line_width(0.4)
    pdf.line(_MARGIN_MM, pdf.get_y() + 1, _PAGE_W_MM - _MARGIN_MM, pdf.get_y() + 1)
    pdf.ln(5)

    # ── Headline callout (optional) ──────────────────────────────────────────
    if snapshot.headline:
        pdf.set_draw_color(*_ACCENT_HEX)
        pdf.set_line_width(1.2)
        y_start = pdf.get_y()
        pdf.line(_MARGIN_MM, y_start, _MARGIN_MM, y_start + 12)  # left border
        pdf.set_text_color(*_INK_HEX)
        pdf.set_font("Times", "B", 13)
        pdf.set_x(_MARGIN_MM + 4)
        pdf.multi_cell(0, 6, _utf8_safe(snapshot.headline))
        pdf.ln(3)

    # ── Body paragraphs (optional) ───────────────────────────────────────────
    if snapshot.body:
        pdf.set_text_color(*_INK_HEX)
        pdf.set_font("Helvetica", "", 10)
        for para in snapshot.body.split("\n\n"):
            para = para.strip()
            if not para:
                continue
            pdf.set_x(_MARGIN_MM)
            pdf.multi_cell(0, 5, _utf8_safe(para))
            pdf.ln(2)

    # ── Sections ─────────────────────────────────────────────────────────────
    for section in snapshot.sections:
        _render_section(pdf, section)

    # ── Footer (last-line on every page via the saved coords) ────────────────
    pdf.set_y(-15)
    pdf.set_draw_color(*_RULE_HEX)
    pdf.line(_MARGIN_MM, pdf.get_y(), _PAGE_W_MM - _MARGIN_MM, pdf.get_y())
    pdf.set_y(-12)
    pdf.set_text_color(*_MUTED_HEX)
    pdf.set_font("Helvetica", "I", 8)
    footer_left = f"Ship Tracker | Generated {generated}"
    pdf.cell(0, 4, _utf8_safe(footer_left), ln=0)
    if snapshot.footer_note:
        pdf.set_x(_MARGIN_MM)
        pdf.set_y(-8)
        pdf.multi_cell(0, 4, _utf8_safe(snapshot.footer_note))

    out = pdf.output(dest="S")
    # Older fpdf returns a str (latin-1), newer returns bytearray.
    if isinstance(out, (bytes, bytearray)):
        return bytes(out)
    return out.encode("latin-1", errors="replace")


def _render_section(pdf, section: ViewSection) -> None:
    """Render one ViewSection onto the current PDF page."""
    if pdf.get_y() > 250:
        pdf.add_page()

    # Section title with hairline above
    pdf.set_draw_color(*_RULE_HEX)
    pdf.set_line_width(0.3)
    y = pdf.get_y() + 3
    pdf.line(_MARGIN_MM, y, _PAGE_W_MM - _MARGIN_MM, y)
    pdf.ln(4)

    pdf.set_text_color(*_INK_HEX)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, _utf8_safe(section.title), ln=1)

    # Body
    if section.body:
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(*_INK_HEX)
        for para in section.body.split("\n\n"):
            para = para.strip()
            if not para:
                continue
            pdf.set_x(_MARGIN_MM)
            pdf.multi_cell(0, 5, _utf8_safe(para))
            pdf.ln(1)

    # Bullets — single multi_cell with the bullet prefix inline so we never
    # end up with a partial-cell cursor that FPDF can't wrap from.
    if section.bullets:
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(*_INK_HEX)
        for bullet in section.bullets:
            pdf.set_x(_MARGIN_MM)
            pdf.multi_cell(0, 5, _utf8_safe("* " + str(bullet)))
        pdf.ln(1)

    # Tables
    for table in section.tables:
        _render_table(pdf, table)
        pdf.ln(2)


def _render_table(pdf, table: ViewTable) -> None:
    """Render a labeled, hairline-ruled table.

    Column widths are split evenly across the printable width. Headers get
    a thin underline and bold weight; rows alternate background lightly for
    readability (simulated with a tiny rule between rows since FPDF's fill
    is finicky on light tints).
    """
    if not table.headers and not table.rows:
        return

    if table.title:
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*_MUTED_HEX)
        pdf.cell(0, 5, _utf8_safe(table.title), ln=1)
        pdf.ln(1)

    n_cols = max(len(table.headers), max((len(r) for r in table.rows), default=0))
    if n_cols == 0:
        return
    width = _PAGE_W_MM - 2 * _MARGIN_MM
    col_w = width / n_cols

    # Header row
    if table.headers:
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*_INK_HEX)
        for h in table.headers:
            pdf.cell(col_w, 5, _utf8_safe(str(h))[:60], border=0, ln=0)
        pdf.ln(5)
        pdf.set_draw_color(*_ACCENT_HEX)
        pdf.set_line_width(0.3)
        y = pdf.get_y()
        pdf.line(_MARGIN_MM, y, _MARGIN_MM + width, y)
        pdf.ln(0.8)

    # Body rows
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*_INK_HEX)
    pdf.set_draw_color(*_RULE_HEX)
    pdf.set_line_width(0.15)
    for row in table.rows:
        for i in range(n_cols):
            cell = str(row[i]) if i < len(row) else ""
            pdf.cell(col_w, 5, _utf8_safe(cell)[:60], border=0, ln=0)
        pdf.ln(5)
        y = pdf.get_y()
        pdf.line(_MARGIN_MM, y, _MARGIN_MM + width, y)
        pdf.ln(0.3)


def render_export_button(
    snapshot: ViewSnapshot,
    filename_prefix: str,
    *,
    key: str,
    label: str = "⇩ Export PDF",
    container_width: bool = True,
) -> None:
    """Streamlit helper — renders a download button bound to ``build_view_pdf``.

    Tab code calls this with a ready-built snapshot; the helper handles the
    try/except wrapping, filename generation, and falls back to a disabled
    button (with the exception in its tooltip) when PDF generation fails.

    ``filename_prefix`` produces ``<prefix>_<YYYY-MM-DD>.pdf``. ``key`` must
    be unique across the Streamlit page — typically ``"<tab>_export_pdf"``.

    Imported only when called (Streamlit isn't a hard dep of this module),
    so the build_view_pdf path remains usable in headless / test contexts.
    """
    import streamlit as st
    from datetime import datetime, timezone

    try:
        pdf_bytes = build_view_pdf(snapshot)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        st.download_button(
            label,
            data=pdf_bytes,
            file_name=f"{filename_prefix}_{today}.pdf",
            mime="application/pdf",
            use_container_width=container_width,
            key=key,
        )
    except Exception as exc:
        st.button(
            label, disabled=True, use_container_width=container_width,
            key=f"{key}_disabled",
            help=f"PDF export unavailable: {exc}",
        )


__all__ = [
    "ViewTable", "ViewSection", "ViewSnapshot",
    "build_view_pdf", "render_export_button",
]
