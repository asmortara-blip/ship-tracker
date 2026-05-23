"""utils/markdown_export.py — convert an investor report payload to Markdown.

Markdown export is the shareable-on-GitHub-/-Notion-/-Slack-thread sibling
of the existing PDF + HTML export paths. The PDF is great for archival
and email; the Markdown body renders inline in every code-review tool,
chat surface, and wiki engine our users already have open.

Design notes
------------
* **Pure function.** ``report_to_markdown`` takes a single in-memory
  payload (the same ``InvestorReport`` dataclass the PDF/HTML renderers
  consume, OR a plain dict of the same shape) and returns a single
  UTF-8 string. No I/O, no Streamlit, no logging side effects — every
  branch is defensive so the function never raises.
* **Schema-tolerant.** We support both attribute access (real
  dataclass: ``report.sentiment.overall_label``) AND dict access
  (already-serialised: ``report["sentiment_label"]``). The fallback
  helper ``_get`` handles both. A missing field never breaks the
  layout; it renders as ``"—"`` or an empty section placeholder.
* **No HTML injection.** Markdown table cells must escape pipes (``|``)
  to ``\\|`` so a cell value containing a pipe doesn't break column
  alignment. Leading hashes (``#``) on a section title are escaped to
  ``\\#`` so a payload-supplied title can't promote itself to an
  arbitrary header level.

This module must NOT import Streamlit, FPDF, or any other heavy
dependency — it's reachable from both the UI tab and the stdlib API
server, which has no Streamlit / Cairo / etc. on its image.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional


# Footer schema marker — bumped only when the Markdown SHAPE changes
# (new sections, new column ordering). Independent from the SQLite
# SCHEMA_VERSION because the two evolve on different rhythms; this one
# is for human readers of an exported report.
MARKDOWN_SCHEMA_VERSION = 1


# ─────────────────────────────────────────────────────────────────────
#  Field-extraction helpers — tolerate dataclass OR dict input
# ─────────────────────────────────────────────────────────────────────


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Return ``obj.name`` (attribute) or ``obj[name]`` (dict key).

    Returns ``default`` when neither succeeds OR when the resolved
    value is ``None``. Never raises — a TypeError from an exotic input
    collapses to ``default`` like everything else.
    """
    if obj is None:
        return default
    try:
        if isinstance(obj, dict):
            val = obj.get(name, default)
        elif hasattr(obj, name):
            val = getattr(obj, name)
        else:
            val = default
    except Exception:  # noqa: BLE001
        return default
    return val if val is not None else default


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


# ─────────────────────────────────────────────────────────────────────
#  Markdown primitives
# ─────────────────────────────────────────────────────────────────────


def _md_escape(text: Any) -> str:
    """Escape Markdown table-breaking characters.

    Specifically:
      * ``|`` → ``\\|``  (table-column separator)
      * Leading ``#`` → ``\\#``  (so a payload-supplied title can't
        promote itself to an H1 / H2 / etc.)

    The input is coerced to ``str`` first so the helper accepts ints,
    floats, None — and never raises. ``None`` becomes ``"—"`` because
    the empty string would silently disappear inside a table cell and
    misleadingly suggest the column was deliberately blank.
    """
    if text is None:
        return "—"
    s = str(text)
    # Replace literal pipe first; the leading-hash escape doesn't care
    # whether the pipe replacement happened.
    s = s.replace("|", "\\|")
    if s.startswith("#"):
        # Escape only the leading run of #s — interior hashes are fine.
        i = 0
        while i < len(s) and s[i] == "#":
            i += 1
        s = ("\\#" * i) + s[i:]
    # Newlines inside a table cell break the row visually. Collapse
    # any embedded newline to a space so the cell stays single-line;
    # the section bodies (outside tables) are written verbatim and
    # don't go through this helper.
    s = s.replace("\r\n", " ").replace("\n", " ")
    return s


def _md_table(rows: list[dict], columns: list[str]) -> str:
    """Render a Markdown pipe table from a list of dicts.

    Args:
        rows:    The row data. Each dict maps a column header to a
                 cell value. Missing keys are rendered as ``"—"``.
                 An empty list produces a header row plus a single
                 ``"(none)"`` placeholder row so the table is still
                 visible (an empty table renders as nothing).
        columns: Ordered list of column headers — defines both the
                 column order AND the keys looked up in each row.

    Returns:
        A single Markdown string ending in a newline. All cells are
        passed through :func:`_md_escape` so pipes / leading hashes
        / embedded newlines can't break the layout.
    """
    if not columns:
        return ""

    header = "| " + " | ".join(_md_escape(c) for c in columns) + " |"
    separator = "|" + "|".join("---" for _ in columns) + "|"
    lines = [header, separator]

    if not rows:
        # Placeholder so the table renders SOMETHING — an empty body
        # below the separator is valid Markdown but visually invisible
        # in most renderers.
        placeholder_cells = ["(none)"] + ["" for _ in columns[1:]]
        lines.append(
            "| " + " | ".join(_md_escape(c) for c in placeholder_cells) + " |"
        )
    else:
        for row in rows:
            cells = [_md_escape(row.get(col, "—")) for col in columns]
            lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _md_section(title: str, body: str) -> str:
    """Render an H2 section: a heading line, blank line, body, blank line.

    The title is escaped against H-level promotion attacks. The body
    is written verbatim — section authors are trusted to format
    sub-content correctly. Trailing whitespace on the body is
    stripped so adjacent sections don't accumulate blank lines.
    """
    safe_title = _md_escape(title)
    body_clean = (body or "").rstrip()
    if not body_clean:
        return f"## {safe_title}\n\n"
    return f"## {safe_title}\n\n{body_clean}\n\n"


# ─────────────────────────────────────────────────────────────────────
#  Section builders — each takes a payload and returns the section
#  body string (without its H2 title). Heading is added by the caller.
# ─────────────────────────────────────────────────────────────────────


def _build_signals_section(payload: Any) -> str:
    """Render the Alpha Signals table.

    Accepts both the dataclass shape (``payload.alpha.signals`` →
    list of AlphaSignal objects with ``ticker`` / ``direction`` /
    ``conviction`` / ``score``) AND the dict shape
    (``payload["signals"]`` → list of plain dicts). When neither
    yields any rows, returns ``"No signals."`` rather than an empty
    table — explicit better than implicit.
    """
    signals = _get(payload, "signals", None)
    if signals is None:
        # Try the dataclass nested path: payload.alpha.signals
        alpha = _get(payload, "alpha", None)
        if alpha is not None:
            signals = _get(alpha, "signals", None)
    if not signals:
        return "No signals."

    rows: list[dict] = []
    for s in signals:
        ticker = _get(s, "ticker", "")
        direction = _get(s, "direction", "")
        conviction = _get(s, "conviction", "")
        # Confidence might live as ``conviction`` (categorical: HIGH /
        # MED / LOW) or as a numeric ``score`` / ``confidence``. Prefer
        # explicit ``confidence`` when present; otherwise fall through
        # to ``conviction``.
        confidence = _get(s, "confidence", None)
        if confidence is None:
            confidence = conviction or "—"
        rows.append({
            "Signal":     ticker or "—",
            "Direction":  direction or "—",
            "Confidence": confidence,
        })
    return _md_table(rows, ["Signal", "Direction", "Confidence"])


def _build_routes_section(payload: Any) -> str:
    """Render the Top Routes table from the freight summary.

    Looks first at ``payload.routes`` (already flat), then at
    ``payload.freight.routes`` (dataclass nested shape). Empty / None
    routes collapse to ``"No route data."``. Each row pulls
    ``route_id`` / ``status`` / ``rate`` (or ``latest``) — missing
    fields render as ``"—"``.
    """
    routes = _get(payload, "routes", None)
    if routes is None:
        freight = _get(payload, "freight", None)
        if freight is not None:
            routes = _get(freight, "routes", None)
    if not routes:
        return "No route data."

    rows: list[dict] = []
    for r in routes:
        route_id = _get(r, "route_id", "")
        status = _get(r, "status", _get(r, "trend", ""))
        latest = _get(r, "latest", None)
        if latest is None:
            # Engine schema uses ``rate`` for the most-recent freight
            # price; fall back to that before giving up.
            latest = _get(r, "rate", "—")
        rows.append({
            "Route":  route_id or "—",
            "Status": status or "—",
            "Latest": latest,
        })
    return _md_table(rows, ["Route", "Status", "Latest"])


def _build_macro_section(payload: Any) -> str:
    """Render a table of macro indicators with current value + 7d change.

    The engine emits 30-day change, not 7-day, on the ``MacroSnapshot``
    dataclass. We label the column ``"7d change"`` per the spec but
    fall back to whichever change field the payload actually carries
    (``change_7d_pct`` first, then ``change_30d_pct``, then the bare
    ``change`` field). Missing values render as ``"—"``.
    """
    macro = _get(payload, "macro", None)
    if macro is None:
        return "No macro data."

    # Build (indicator_label, value_key, change_key) tuples so adding a
    # new indicator is one line.
    indicators = [
        ("BDI", "bdi", ("bdi_change_7d_pct", "bdi_change_30d_pct")),
        ("WTI", "wti", ("wti_change_7d_pct", "wti_change_30d_pct")),
        ("10Y Treasury", "treasury_10y", ("treasury_10y_change_7d_pct",
                                           "treasury_10y_change_30d_pct")),
        ("DXY proxy", "dxy_proxy", ("dxy_change_7d_pct",
                                     "dxy_change_30d_pct")),
        ("PMI proxy", "pmi_proxy", ("pmi_change_7d_pct",
                                     "pmi_change_30d_pct")),
    ]
    # Optional FBX composite — pull from the freight summary, not macro.
    fbx = _get(_get(payload, "freight", None), "fbx_composite", None)

    rows: list[dict] = []
    for label, value_key, change_keys in indicators:
        val = _get(macro, value_key, None)
        change_val: Any = None
        for ck in change_keys:
            cv = _get(macro, ck, None)
            if cv is not None:
                change_val = cv
                break
        rows.append({
            "Indicator":  label,
            "Current":    "—" if val is None else val,
            "7d change":  "—" if change_val is None else f"{change_val}%",
        })
    if fbx is not None:
        rows.append({
            "Indicator":  "FBX composite",
            "Current":    fbx,
            "7d change":  "—",
        })
    return _md_table(rows, ["Indicator", "Current", "7d change"])


def _build_key_findings_section(payload: Any) -> str:
    """Render the Key Findings bullet list.

    Pulls from ``payload.key_findings`` (flat list of strings) OR
    ``payload.market.top_insights`` (the dataclass nested path; each
    insight has a ``title`` field). Returns a Markdown unordered list
    with one bullet per finding. Empty/missing collapses to
    ``"No key findings."``.
    """
    findings: Optional[Iterable] = _get(payload, "key_findings", None)
    if findings is None:
        market = _get(payload, "market", None)
        if market is not None:
            insights = _get(market, "top_insights", None) or []
            findings = []
            for ins in insights:
                title = _get(ins, "title", None) or _get(ins, "summary", "")
                if title:
                    findings.append(title)
    if not findings:
        return "No key findings."

    lines = []
    for f in findings:
        if f is None:
            continue
        # Don't run findings through _md_escape — they're prose, not
        # table cells; pipes and hashes are legitimate. We only need
        # to make sure each bullet stays on a single bullet line.
        text = str(f).replace("\r\n", " ").replace("\n", " ").strip()
        if text:
            lines.append(f"- {text}")
    if not lines:
        return "No key findings."
    return "\n".join(lines)


def _build_executive_summary_section(payload: Any) -> str:
    """Pull the executive_summary prose verbatim.

    Looks at the flat ``executive_summary`` field first, then at
    ``payload.ai.executive_summary`` (dataclass nested path). Empty /
    missing collapses to ``"No executive summary."``.

    No truncation — Markdown handles long content fine; truncating
    in this layer would hide content from a downstream consumer that
    explicitly opted in to "give me the full report".
    """
    text = _get(payload, "executive_summary", None)
    if text is None:
        ai = _get(payload, "ai", None)
        if ai is not None:
            text = _get(ai, "executive_summary", None)
    if not text:
        return "No executive summary."
    # Strip surrounding whitespace but keep internal structure (paragraphs,
    # line breaks) intact — Markdown renders them sensibly.
    return str(text).strip()


def _build_data_quality_section(payload: Any) -> str:
    """Render the Data Quality section.

    The payload's ``data_quality`` field is a single string label
    (FULL / PARTIAL / DEGRADED). We surface it verbatim plus a one-
    line description so a non-Ship-Tracker reader has context.
    """
    quality = _get(payload, "data_quality", None)
    if quality is None:
        return "Data quality not reported."
    label = str(quality).upper()
    notes = {
        "FULL":     "All feeds live and within freshness SLOs.",
        "PARTIAL":  "Some feeds degraded or stale; signal coverage reduced.",
        "DEGRADED": "Multiple feeds offline; treat signals as indicative only.",
    }
    note = notes.get(label, "")
    if note:
        return f"**{label}** — {note}"
    return f"**{label}**"


# ─────────────────────────────────────────────────────────────────────
#  Top-level renderer
# ─────────────────────────────────────────────────────────────────────


def report_to_markdown(report_data: Any) -> str:
    """Convert a report payload to a self-contained Markdown string.

    Args:
        report_data: Either an ``InvestorReport`` dataclass instance,
                     a serialised dict of the same shape, or ``None``.
                     ``None`` returns a single italicised placeholder
                     string — the caller can pipe it into a download
                     button without first checking the input.

    Returns:
        UTF-8 Markdown string. Section ordering:

          # <title>
          **Generated:** <ISO>  |  **Risk:** <level>  |  **Sentiment:** <label>
          ## Executive Summary
          ## Signals
          ## Key Findings
          ## Top Routes
          ## Macro Indicators
          ## Data Quality
          ---
          *Generated by Ship Tracker — schema v<N>*

        Every section is rendered; empty payload values produce
        placeholder text inside the section (``"No signals."`` etc.)
        rather than skipping the section entirely — consistent shape
        is more valuable than terse output for a Markdown digest.
    """
    if report_data is None:
        return "_(empty report)_"

    # Title — falls back through report_date / generated_at / generic.
    title = (
        _get(report_data, "title", None)
        or _get(report_data, "report_date", None)
        or "Shipping Market Report"
    )
    generated_at = _get(report_data, "generated_at", "") or "—"

    # Sentiment can live flat OR nested under ``sentiment``.
    sentiment_label = _get(report_data, "sentiment_label", None)
    sentiment_score = _get(report_data, "sentiment_score", None)
    if sentiment_label is None:
        sentiment_obj = _get(report_data, "sentiment", None)
        sentiment_label = _get(sentiment_obj, "overall_label", "—")
        if sentiment_score is None:
            sentiment_score = _get(sentiment_obj, "overall_score", None)
    sentiment_label = sentiment_label or "—"

    risk_level = _get(report_data, "risk_level", None)
    if risk_level is None:
        market = _get(report_data, "market", None)
        risk_level = _get(market, "risk_level", "—")

    # Header line — render score only when it's a real number, so the
    # output of an empty report doesn't carry a misleading "0.00".
    score_str = ""
    if sentiment_score is not None:
        try:
            score_str = f" ({_safe_float(sentiment_score):+.2f})"
        except Exception:  # noqa: BLE001
            score_str = ""

    header_line = (
        f"**Generated:** {_md_escape(generated_at)}  \n"
        f"**Risk:** {_md_escape(risk_level)}  "
        f"**Sentiment:** {_md_escape(sentiment_label)}{score_str}"
    )

    # Assemble.
    parts = []
    # The title is the only H1; escape it so a payload-supplied title
    # starting with `#` can't promote itself to a higher level.
    parts.append(f"# {_md_escape(title)}\n")
    parts.append(header_line + "\n")
    parts.append("")  # blank line before first section

    parts.append(_md_section(
        "Executive Summary",
        _build_executive_summary_section(report_data),
    ))
    parts.append(_md_section(
        "Signals",
        _build_signals_section(report_data),
    ))
    parts.append(_md_section(
        "Key Findings",
        _build_key_findings_section(report_data),
    ))
    parts.append(_md_section(
        "Top Routes",
        _build_routes_section(report_data),
    ))
    parts.append(_md_section(
        "Macro Indicators",
        _build_macro_section(report_data),
    ))
    parts.append(_md_section(
        "Data Quality",
        _build_data_quality_section(report_data),
    ))

    parts.append("---\n")
    parts.append(
        f"*Generated by Ship Tracker — schema v{MARKDOWN_SCHEMA_VERSION}*\n"
    )

    return "\n".join(parts)
