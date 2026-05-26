"""delivery/port_supply_shock_digest.py — overnight port-supply digest.

Renders the structured ``DiffReport`` produced by the daily port-supply
snapshot job (``processing.port_supply_history.run_daily_snapshot_job``)
into three ready-to-send artifacts:

  * ``render_html``       — single-page inline-styled HTML document with
                            one ``<table>`` per non-empty diff bucket.
                            Designed to survive Gmail / Outlook / Apple
                            Mail style stripping (every style attribute
                            lives on the element, no ``<style>`` block).
  * ``render_plain_text`` — same content as plain text. Used as the
                            ``text/plain`` part of multipart email AND
                            as the body for Slack / webhook channels.
  * ``build_subject_line`` — short subject line summarising the diff
                             ("Port-Supply: 3 severity shifts, 2 entered
                             deficit (2026-05-26)"). Empty diff produces
                             "Port-Supply: no material changes
                             (2026-05-26)".

Plus a gating helper:

  * ``should_send``       — True iff at least one of the five diff
                            buckets has at least one entry. Operators
                            don't want inbox spam on quiet days; this
                            lets the scheduler skip artifact persistence
                            (and any downstream channel skip dispatch).

The five buckets, in display order:

  1. ``severity_shifts``  — every port whose severity band changed
  2. ``entered_deficit``  — ports that crossed into deficit overnight
  3. ``exited_deficit``   — ports that crossed back out of deficit
  4. ``deficit_moves``    — material |Δ days| moves above the threshold
  5. ``ticker_shuffles``  — top-exposed-ticker reshuffles

Design constraints
------------------
* Pure rendering — no SMTP, no HTTP, no file I/O. The artifacts are
  the deliverable; the channels are downstream. Tested without any
  network mocks.
* No new dependencies — stdlib only. CSS inline; tables hand-written.
* Empty buckets are SUPPRESSED from the HTML / text bodies (no
  "Entered deficit: (none)" filler). The diff that gets sent should
  be the diff that matters.
"""
from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools.port_supply_diff import DiffReport, PortDelta


__all__ = [
    "render_html",
    "render_plain_text",
    "build_subject_line",
    "should_send",
]


# ─── Palette ──────────────────────────────────────────────────────────────
# WSJ-ish steel + amber + red. Mirrors engine.operator_digest so the
# look stays consistent across the platform's email surface area.

_COLOR_TEXT = "#24292e"
_COLOR_MUTED = "#586069"
_COLOR_BG = "#ffffff"
_COLOR_ACCENT_BG = "#fafbfc"
_COLOR_BORDER = "#e1e4e8"
_COLOR_STEEL = "#2a3b4d"
_COLOR_RED = "#d73a49"
_COLOR_AMBER = "#f97316"
_COLOR_GREEN = "#22863a"


# ─── Gating ────────────────────────────────────────────────────────────────


def _bucket_counts(diff: "DiffReport") -> dict[str, int]:
    """Return per-bucket entry counts as a plain dict.

    Returns zeros for every bucket if ``diff`` is None — the caller
    treats an absent diff as "no material changes".
    """
    if diff is None:
        return {
            "severity_shifts": 0,
            "entered_deficit": 0,
            "exited_deficit":  0,
            "deficit_moves":   0,
            "ticker_shuffles": 0,
        }
    return {
        "severity_shifts": len(diff.severity_shifts),
        "entered_deficit": len(diff.entered_deficit),
        "exited_deficit":  len(diff.exited_deficit),
        "deficit_moves":   len(diff.deficit_moves),
        "ticker_shuffles": len(diff.ticker_shuffles),
    }


def should_send(diff: "DiffReport | None") -> bool:
    """True iff the diff has at least one entry across the five buckets.

    Used by the scheduler to gate persistence — quiet days produce no
    digest artifacts so downstream channels have nothing to dispatch.

    Treats a ``None`` diff (e.g. first-ever run with nothing prior)
    as a quiet day.
    """
    counts = _bucket_counts(diff)
    return any(v > 0 for v in counts.values())


# ─── Subject line ──────────────────────────────────────────────────────────


def build_subject_line(
    diff: "DiffReport | None",
    container_type: str = "40FT_DRY",
    snapshot_date_iso: str = "",
) -> str:
    """Short subject line summarising the diff.

    Examples:
      * "Port-Supply: no material changes (2026-05-26)"
      * "Port-Supply: 3 severity shifts, 2 entered deficit (2026-05-26)"
      * "Port-Supply: 1 entered deficit (2026-05-26)"

    The pieces are joined as ", " with no Oxford comma — keeps the
    subject scannable in a crowded inbox. Date is omitted from the
    parenthetical when ``snapshot_date_iso`` is empty.
    """
    date_tail = f" ({snapshot_date_iso})" if snapshot_date_iso else ""
    counts = _bucket_counts(diff)

    if not any(v > 0 for v in counts.values()):
        return f"Port-Supply: no material changes{date_tail}"

    # Build short pieces in the same order as the body for readability.
    pieces: list[str] = []
    if counts["severity_shifts"]:
        n = counts["severity_shifts"]
        pieces.append(f"{n} severity shift" + ("s" if n != 1 else ""))
    if counts["entered_deficit"]:
        n = counts["entered_deficit"]
        # "entered deficit" reads fine either count — keep it singular-
        # invariant since "entered deficits" is awkward.
        pieces.append(f"{n} entered deficit")
    if counts["exited_deficit"]:
        n = counts["exited_deficit"]
        pieces.append(f"{n} exited deficit")
    if counts["deficit_moves"]:
        n = counts["deficit_moves"]
        pieces.append(f"{n} material move" + ("s" if n != 1 else ""))
    if counts["ticker_shuffles"]:
        n = counts["ticker_shuffles"]
        pieces.append(f"{n} ticker reshuffle" + ("s" if n != 1 else ""))

    return f"Port-Supply: {', '.join(pieces)}{date_tail}"


# ─── HTML rendering ────────────────────────────────────────────────────────


def _header_html(
    counts: dict[str, int],
    container_type: str,
    snapshot_date_iso: str,
    prior_date_iso: str,
) -> str:
    """Status-banded header. Accent color tracks how disruptive the
    diff is — any severity shift OR entered-deficit transition warrants
    a red accent; otherwise material moves / reshuffles use amber; an
    all-empty diff uses green ("quiet")."""
    total = sum(counts.values())
    if counts["severity_shifts"] > 0 or counts["entered_deficit"] > 0:
        accent = _COLOR_RED
        status_label = "ATTENTION"
    elif total > 0:
        accent = _COLOR_AMBER
        status_label = "WATCH"
    else:
        accent = _COLOR_GREEN
        status_label = "QUIET"

    date_str = escape(snapshot_date_iso) or "—"
    prior_str = escape(prior_date_iso) or "—"
    container_str = escape(container_type)

    return (
        f"<div style=\"border-left:6px solid {accent};padding:14px 18px;"
        f"background:{_COLOR_ACCENT_BG};\">"
        f"<div style=\"font-size:11px;font-weight:700;letter-spacing:0.12em;"
        f"text-transform:uppercase;color:{accent};\">"
        f"Port-Supply Shock Digest &middot; {status_label}"
        f"</div>"
        f"<h2 style=\"margin:4px 0 0 0;font-size:20px;color:{_COLOR_TEXT};\">"
        f"Overnight diff &mdash; {date_str}"
        f"</h2>"
        f"<div style=\"font-size:12px;color:{_COLOR_MUTED};margin-top:4px;\">"
        f"Container type: {container_str} &middot; "
        f"vs prior snapshot: {prior_str}"
        f"</div>"
        f"</div>"
    )


def _table_open(headers: list[str]) -> str:
    """Open a <table> with WSJ-styled header row."""
    head_cells = "".join(
        f"<th style=\"text-align:left;padding:6px 8px;border-bottom:2px "
        f"solid {_COLOR_BORDER};font-size:11px;letter-spacing:0.05em;"
        f"text-transform:uppercase;color:{_COLOR_MUTED};\">{escape(h)}</th>"
        for h in headers
    )
    return (
        f"<table style=\"width:100%;border-collapse:collapse;margin:6px 0 0 0;"
        f"font-family:Helvetica,Arial,sans-serif;font-size:13px;"
        f"color:{_COLOR_TEXT};\">"
        f"<thead><tr>{head_cells}</tr></thead><tbody>"
    )


def _row_cell(text: str, *, mono: bool = False, align: str = "left") -> str:
    """One <td>. ``mono`` switches to a monospaced font (used for ticker
    lists + locodes). ``align`` accepts left / right / center."""
    family = "Menlo,Consolas,monospace" if mono else (
        "Helvetica,Arial,sans-serif"
    )
    return (
        f"<td style=\"padding:6px 8px;border-bottom:1px solid {_COLOR_BORDER};"
        f"text-align:{align};font-family:{family};vertical-align:top;\">"
        f"{escape(text)}"
        f"</td>"
    )


def _section_open(title: str, count: int) -> str:
    """One section header — small caps label with the count."""
    return (
        f"<div style=\"margin-top:22px;\">"
        f"<div style=\"font-size:12px;font-weight:700;letter-spacing:0.08em;"
        f"text-transform:uppercase;color:{_COLOR_STEEL};"
        f"border-bottom:1px solid {_COLOR_BORDER};padding-bottom:4px;\">"
        f"{escape(title)} &middot; {count}"
        f"</div>"
    )


def _section_close() -> str:
    return "</div>"


def _severity_section_html(deltas: "list[PortDelta]") -> str:
    if not deltas:
        return ""
    rows: list[str] = []
    for d in deltas:
        rows.append(
            "<tr>"
            + _row_cell(d.locode, mono=True)
            + _row_cell(d.name or "")
            + _row_cell(d.severity_before or "")
            + _row_cell(d.severity_after or "")
            + _row_cell(f"{d.deficit_before:+.1f}d", align="right")
            + _row_cell(f"{d.deficit_after:+.1f}d", align="right")
            + _row_cell(f"{d.deficit_delta:+.1f}d", align="right")
            + "</tr>"
        )
    return (
        _section_open("Severity shifts", len(deltas))
        + _table_open(
            ["Locode", "Port", "Before band", "After band",
             "Before", "After", "Δ"]
        )
        + "".join(rows)
        + "</tbody></table>"
        + _section_close()
    )


def _entered_or_exited_section_html(
    title: str, deltas: "list[PortDelta]",
) -> str:
    if not deltas:
        return ""
    rows: list[str] = []
    for d in deltas:
        rows.append(
            "<tr>"
            + _row_cell(d.locode, mono=True)
            + _row_cell(d.name or "")
            + _row_cell(d.region or "")
            + _row_cell(f"{d.deficit_before:+.1f}d", align="right")
            + _row_cell(f"{d.deficit_after:+.1f}d", align="right")
            + _row_cell(f"{d.deficit_delta:+.1f}d", align="right")
            + "</tr>"
        )
    return (
        _section_open(title, len(deltas))
        + _table_open(
            ["Locode", "Port", "Region", "Before", "After", "Δ"]
        )
        + "".join(rows)
        + "</tbody></table>"
        + _section_close()
    )


def _deficit_moves_section_html(deltas: "list[PortDelta]") -> str:
    if not deltas:
        return ""
    rows: list[str] = []
    for d in deltas:
        rows.append(
            "<tr>"
            + _row_cell(d.locode, mono=True)
            + _row_cell(d.name or "")
            + _row_cell(d.region or "")
            + _row_cell(f"{d.deficit_before:+.1f}d", align="right")
            + _row_cell(f"{d.deficit_after:+.1f}d", align="right")
            + _row_cell(f"{d.deficit_delta:+.1f}d", align="right")
            + "</tr>"
        )
    return (
        _section_open("Material deficit moves", len(deltas))
        + _table_open(
            ["Locode", "Port", "Region", "Before", "After", "Δ"]
        )
        + "".join(rows)
        + "</tbody></table>"
        + _section_close()
    )


def _ticker_shuffles_section_html(deltas: "list[PortDelta]") -> str:
    if not deltas:
        return ""
    rows: list[str] = []
    for d in deltas:
        added = ", ".join(d.tickers_added) if d.tickers_added else "—"
        removed = ", ".join(d.tickers_removed) if d.tickers_removed else "—"
        rows.append(
            "<tr>"
            + _row_cell(d.locode, mono=True)
            + _row_cell(d.name or "")
            + _row_cell(added, mono=True)
            + _row_cell(removed, mono=True)
            + "</tr>"
        )
    return (
        _section_open("Ticker reshuffles", len(deltas))
        + _table_open(["Locode", "Port", "Added", "Removed"])
        + "".join(rows)
        + "</tbody></table>"
        + _section_close()
    )


def render_html(
    diff: "DiffReport | None",
    container_type: str = "40FT_DRY",
    snapshot_date_iso: str = "",
    prior_date_iso: str = "",
) -> str:
    """Render the diff as a complete inline-styled HTML document.

    Each non-empty diff bucket gets one ``<table>`` section; empty
    buckets are SUPPRESSED entirely (no "(none)" filler). A diff that
    is empty across all buckets — or a ``None`` diff — produces a
    one-line confirmation body so the digest is still valid HTML.
    """
    counts = _bucket_counts(diff)
    header = _header_html(
        counts, container_type, snapshot_date_iso, prior_date_iso,
    )

    if not any(v > 0 for v in counts.values()):
        body_inner = (
            f"{header}"
            f"<div style=\"margin-top:18px;padding:12px 14px;"
            f"border-left:3px solid {_COLOR_GREEN};"
            f"background:{_COLOR_ACCENT_BG};color:{_COLOR_TEXT};"
            f"font-size:13px;\">"
            f"No material changes overnight."
            f"</div>"
        )
    else:
        sections = [
            _severity_section_html(diff.severity_shifts),
            _entered_or_exited_section_html(
                "Entered deficit", diff.entered_deficit,
            ),
            _entered_or_exited_section_html(
                "Exited deficit", diff.exited_deficit,
            ),
            _deficit_moves_section_html(diff.deficit_moves),
            _ticker_shuffles_section_html(diff.ticker_shuffles),
        ]
        body_inner = header + "".join(sections)

    return (
        "<!DOCTYPE html>"
        "<html><head><meta charset=\"utf-8\">"
        "<title>Port-Supply Shock Digest</title>"
        "</head>"
        f"<body style=\"font-family:Helvetica,Arial,sans-serif;"
        f"color:{_COLOR_TEXT};background:{_COLOR_BG};margin:0;padding:0;\">"
        f"<div style=\"max-width:880px;margin:0 auto;padding:24px;\">"
        f"{body_inner}"
        f"</div></body></html>"
    )


# ─── Plain-text rendering ──────────────────────────────────────────────────


def _text_section(title: str, deltas: "list[PortDelta]") -> list[str]:
    """One section's worth of plain-text lines (empty list if no entries)."""
    if not deltas:
        return []
    out: list[str] = [
        f"-- {title} ({len(deltas)}) --",
    ]
    for d in deltas:
        if title == "Ticker reshuffles":
            added = ", ".join(d.tickers_added) if d.tickers_added else "-"
            removed = ", ".join(d.tickers_removed) if d.tickers_removed else "-"
            out.append(
                f"  {d.locode:7} {(d.name or '')[:32]:32} "
                f"+[{added}]  -[{removed}]"
            )
        elif title == "Severity shifts":
            out.append(
                f"  {d.locode:7} {(d.name or '')[:32]:32} "
                f"{d.severity_before or '-':20} -> "
                f"{d.severity_after or '-':20} "
                f"({d.deficit_before:+.1f}d -> {d.deficit_after:+.1f}d, "
                f"d{d.deficit_delta:+.1f}d)"
            )
        else:
            out.append(
                f"  {d.locode:7} {(d.name or '')[:32]:32} "
                f"({d.deficit_before:+.1f}d -> {d.deficit_after:+.1f}d, "
                f"d{d.deficit_delta:+.1f}d)"
            )
    out.append("")
    return out


def render_plain_text(
    diff: "DiffReport | None",
    container_type: str = "40FT_DRY",
    snapshot_date_iso: str = "",
    prior_date_iso: str = "",
) -> str:
    """Same content as ``render_html`` but plain text.

    Used as the ``text/plain`` part of multipart email AND as the body
    for Slack / webhook channels that want the human-readable form.
    Empty buckets are suppressed (same shape as the HTML body).
    """
    counts = _bucket_counts(diff)
    date_str = snapshot_date_iso or "-"
    prior_str = prior_date_iso or "-"
    container_str = container_type

    lines: list[str] = [
        "Port-Supply Shock Digest",
        "=" * 56,
        f"Snapshot date:  {date_str}",
        f"Prior snapshot: {prior_str}",
        f"Container type: {container_str}",
        "",
    ]

    if not any(v > 0 for v in counts.values()):
        lines.append("No material changes overnight.")
        return "\n".join(lines)

    lines.append(
        f"Counts:  severity_shifts={counts['severity_shifts']} "
        f"entered={counts['entered_deficit']} "
        f"exited={counts['exited_deficit']} "
        f"moves={counts['deficit_moves']} "
        f"reshuffles={counts['ticker_shuffles']}"
    )
    lines.append("")

    lines.extend(_text_section("Severity shifts", diff.severity_shifts))
    lines.extend(_text_section("Entered deficit", diff.entered_deficit))
    lines.extend(_text_section("Exited deficit", diff.exited_deficit))
    lines.extend(_text_section("Material deficit moves", diff.deficit_moves))
    lines.extend(_text_section("Ticker reshuffles", diff.ticker_shuffles))

    return "\n".join(lines).rstrip() + "\n"
