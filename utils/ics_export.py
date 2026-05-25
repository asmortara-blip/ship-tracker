"""utils/ics_export.py — render correlated alert-incidents as an
iCalendar (.ics) feed per RFC 5545.

The goal here is one-time-setup operator visibility into shipping
incidents. An operator subscribes their calendar app (Google
Calendar / Apple Calendar / Outlook / Thunderbird) to the public
``/api/v1/incidents.ics?token=…`` URL once and from then on
incidents show up alongside their meetings — no dashboard
poll-and-refresh loop, no inbox flood from per-alert emails.

This module is the renderer half of that surface. It is intentionally
pure: take a list of correlated ``AlertIncident`` dataclasses
(from :mod:`engine.alert_correlator`), emit a fully-compliant
iCalendar string. The API server holds the HTTP / auth layer; the
calendar-token module holds the subscription-token plumbing; this
module is just the wire-format converter.

RFC 5545 compliance notes
-------------------------
The spec is fussy in three places that trip up naive implementations:

* **Line endings.** Every line of an iCalendar object is terminated
  with CRLF (``\\r\\n``) — even the last one. Most calendar apps
  tolerate bare LF but Apple Calendar and Outlook reject feeds with
  inconsistent line endings outright.
* **Line folding.** Logical lines longer than 75 *octets* (not
  characters — multibyte UTF-8 sequences count their bytes) must be
  wrapped to a continuation line that begins with a single SPACE.
  The parser un-folds by joining ``CRLF SP`` back together. We fold
  on octet boundaries, never inside a UTF-8 sequence, so a Japanese
  port name doesn't get sliced through a multibyte codepoint.
* **Text escaping.** Inside ``DESCRIPTION``, ``SUMMARY``,
  ``LOCATION``, ``COMMENT`` values the characters ``\\``, ``,``,
  ``;`` must be backslash-escaped, and embedded newlines must be
  emitted as the two-character sequence ``\\n``. Failure to escape
  a comma in a description silently turns it into a list separator
  and the parser drops half the field.

The module has zero third-party dependencies — stdlib only. Tests can
parse the output with the optional ``icalendar`` package if it's
installed; we never require it.

Defensive rendering
-------------------
Calendar feeds are served unauthenticated (the token IS the secret).
A 500 on the ICS endpoint would be a noisy public-facing surprise
that a malicious caller could probe for stack traces. Every helper
here is wrapped in try/except → graceful fallback (empty string,
``Unknown`` placeholder, etc.) so a bad incident shape (missing
``start_at``, ``severity_max=None``, …) downgrades to a less
informative event rather than crashing the renderer.

The public ``incidents_to_ics`` NEVER raises.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional


# ─────────────────────────────────────────────────────────────────────────────
#  Module constants
# ─────────────────────────────────────────────────────────────────────────────

# CRLF terminator — RFC 5545 §3.1 mandates this exact sequence
# between content lines, including after the final line.
_CRLF: str = "\r\n"

# RFC 5545 §3.1: content lines SHOULD be no longer than 75 octets.
# We pick 75 (not 74, not 76) to match the spec literally — most
# parsers tolerate slightly longer but the strictest ones (Apple
# Calendar's older implementations) reject anything over 75.
_MAX_OCTETS_PER_LINE: int = 75

# Default UID-domain suffix. The UID has to be globally unique per
# RFC 5545 §3.8.4.7; the convention is ``<id>@<authority>``. We use
# ``shiptracker.local`` because the actual deploy host is unknown
# at render time — what matters is that the same incident always
# emits the same UID so calendar apps update events in place
# instead of duplicating them.
_UID_DOMAIN: str = "shiptracker.local"

# Default event duration when an incident has no ``end_at`` —
# AlertIncident does not currently carry one (it's a started_at
# anchor + a member-alerts list), so every event gets DTSTART +
# DTSTART + 1h as a sensible "this was a moment in time" default.
_DEFAULT_DURATION_HOURS: int = 1

# Severity → prefix mapping used in SUMMARY lines. Calendar UIs
# typically truncate long event titles, so the prefix MUST sit at
# the front for the operator to see the severity in a glance.
_SEVERITY_PREFIX: dict[str, str] = {
    "CRITICAL": "[CRITICAL]",
    "HIGH":     "[HIGH]",
    "MEDIUM":   "[MEDIUM]",
    "LOW":      "[LOW]",
}


# ─────────────────────────────────────────────────────────────────────────────
#  Internal helpers — escape / fold / format
# ─────────────────────────────────────────────────────────────────────────────

def _escape_ics_text(s: str) -> str:
    """Escape a string for use inside an iCalendar TEXT value (RFC 5545 §3.3.11).

    The four characters with special meaning in TEXT values are:

    ============ =========================
    Source char  Encoded as
    ============ =========================
    ``\\``       ``\\\\``
    ``;``        ``\\;``
    ``,``        ``\\,``
    NEWLINE      ``\\n``  (LITERAL — the two characters '\\' then 'n')
    ============ =========================

    Backslash MUST be escaped FIRST so we don't double-escape the
    backslashes we just introduced for the other three substitutions.
    Empty / non-string input → empty string (a None severity, a
    missing title, etc.).

    Round-trip safe: the standard library has no built-in
    ical-unescape, but the inverse mapping (``\\n`` → newline,
    ``\\,`` → ``,``, …) is straightforward enough that tests can
    confirm reversibility.
    """
    if not isinstance(s, str):
        return ""
    if not s:
        return ""
    # Order matters: backslash FIRST so we don't double-escape.
    out = s.replace("\\", "\\\\")
    out = out.replace(";", "\\;")
    out = out.replace(",", "\\,")
    # CRLF first so a CR-LF pair doesn't produce \n\n.
    out = out.replace("\r\n", "\\n")
    out = out.replace("\n", "\\n")
    out = out.replace("\r", "\\n")
    return out


def _fold_long_lines(s: str) -> str:
    """Apply RFC 5545 §3.1 line folding to a multi-line iCalendar string.

    Any content line longer than 75 *octets* (not characters — UTF-8
    sequences count by byte) is split: the head stays in place, the
    tail moves to a new line beginning with ``CRLF`` + a single SP.
    A long tail may itself need further folding; the loop handles
    that by walking the wrapped tail.

    The split is octet-aware so it never lands in the middle of a
    multibyte UTF-8 sequence — important when a port name contains
    Cyrillic or Asian characters. We back off the cut point one
    byte at a time until the head ends on a complete codepoint
    boundary (i.e. the next byte is NOT a continuation byte
    ``10xxxxxx``).

    The input is expected to already use CRLF line endings. Lines
    that are already ≤75 octets pass through untouched. Empty input
    → empty output.
    """
    if not s:
        return ""
    out_lines: list[str] = []
    # Split on CRLF (the canonical iCal terminator) but tolerate
    # naked LF on input in case a caller hand-built lines without
    # CRLF.  We re-emit with CRLF unconditionally.
    raw_lines = s.replace("\r\n", "\n").split("\n")
    for line in raw_lines:
        if not line:
            out_lines.append("")
            continue
        # Octet length — UTF-8 byte count is what RFC 5545 measures.
        b = line.encode("utf-8")
        if len(b) <= _MAX_OCTETS_PER_LINE:
            out_lines.append(line)
            continue
        # Walk the octet stream, emitting head chunks ≤75 octets
        # (or ≤74 for continuation lines — the leading SP counts).
        head_limit = _MAX_OCTETS_PER_LINE
        # First chunk fits up to 75 octets; continuation chunks
        # have a 1-octet SP prefix so they fit up to 74 of payload.
        first = True
        idx = 0
        n = len(b)
        chunks: list[bytes] = []
        while idx < n:
            limit = head_limit if first else (_MAX_OCTETS_PER_LINE - 1)
            end = min(idx + limit, n)
            # Walk back if we cut mid-codepoint. UTF-8 continuation
            # bytes match ``10xxxxxx`` (0x80..0xBF); the boundary
            # byte we want to land BEFORE is one that does NOT
            # match that pattern.
            if end < n:
                while end > idx and (b[end] & 0xC0) == 0x80:
                    end -= 1
            # If we couldn't back off (a malformed sequence), just
            # cut where we were — better to emit a slightly oversize
            # line than to loop forever.
            if end == idx:
                end = min(idx + limit, n)
            chunks.append(b[idx:end])
            idx = end
            first = False
        # Reassemble. First chunk stays as-is; every subsequent
        # chunk gets a single SP prefix per RFC 5545 §3.1.
        out_lines.append(chunks[0].decode("utf-8", errors="replace"))
        for c in chunks[1:]:
            out_lines.append(" " + c.decode("utf-8", errors="replace"))
    return _CRLF.join(out_lines)


def _format_ics_datetime(dt: Any) -> str:
    """Format a datetime / ISO-string as iCalendar UTC: ``YYYYMMDDTHHMMSSZ``.

    Per RFC 5545 §3.3.5, this "form #2" UTC format is the simplest
    representation and the most universally supported by calendar
    parsers (everything understands ``Z``-suffixed UTC; not every
    parser understands ``VTIMEZONE``-referenced local times).

    Accepts:
      * ``datetime`` (naive or aware) — naive is assumed UTC.
      * ISO 8601 string (``2026-05-22T18:00:00Z`` or with offset).
      * Anything else → empty string (caller is expected to skip
        the field rather than emit malformed output).
    """
    if dt is None:
        return ""
    try:
        if isinstance(dt, datetime):
            d = dt
        elif isinstance(dt, str):
            if not dt:
                return ""
            # ``fromisoformat`` accepts +00:00 but not the bare 'Z'
            # shorthand until Python 3.11; we normalise either way.
            try:
                d = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            except ValueError:
                return ""
        else:
            return ""
        # Coerce to UTC. A naive datetime is assumed to BE UTC —
        # the AlertIncident.started_at field stores ISO timestamps
        # that get a Z or +00:00 suffix from the engine, so naive
        # would only happen on a malformed input.
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        else:
            d = d.astimezone(timezone.utc)
        return d.strftime("%Y%m%dT%H%M%SZ")
    except Exception:
        # NEVER raise from the formatter — empty string lets the
        # caller emit ``DTSTART:`` which the parser tolerates more
        # gracefully than a malformed value.
        return ""


def _now_utc_ics() -> str:
    """Current wall-clock as an iCalendar UTC timestamp. Helper for
    DTSTAMP so the same NOW is reused across all VEVENTs in one
    render pass — keeps the output byte-stable when nothing has
    changed across two consecutive fetches a millisecond apart."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _add_hours(ics_dt: str, hours: int) -> str:
    """Add ``hours`` to an iCalendar UTC timestamp, returning the
    same iCalendar UTC format. Used to derive DTEND from DTSTART
    when an incident has no explicit end. Empty input → empty
    output."""
    if not ics_dt:
        return ""
    try:
        d = datetime.strptime(ics_dt, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return (d + timedelta(hours=hours)).strftime("%Y%m%dT%H%M%SZ")
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────────────────
#  Incident → VEVENT projection
# ─────────────────────────────────────────────────────────────────────────────

def _incident_field(inc: Any, name: str, default: Any = "") -> Any:
    """Fetch a field from an incident with a default. Works for both
    dataclass instances (``getattr`` on attribute names) and dicts
    (``[]`` lookup) so tests can hand the renderer either shape
    without a conversion step.
    """
    try:
        if isinstance(inc, dict):
            return inc.get(name, default)
        return getattr(inc, name, default)
    except Exception:
        return default


def _incident_uid(inc: Any) -> str:
    """Build the UID line value for one incident.

    UID = ``<incident_id>@<_UID_DOMAIN>``. Falls back to a hash of
    ``started_at`` if ``incident_id`` is missing — keeps the UID
    deterministic for the same input even on malformed data, so a
    re-fetched feed updates the same calendar event instead of
    creating a duplicate.
    """
    inc_id = _incident_field(inc, "incident_id", "")
    if not inc_id:
        # Fallback — use started_at as a stable surrogate. NEVER
        # emit an empty UID; calendar apps reject events without one.
        inc_id = str(_incident_field(inc, "started_at", "unknown"))
    return f"{inc_id}@{_UID_DOMAIN}"


def _incident_summary(inc: Any) -> str:
    """Build the SUMMARY (event title) text for one incident.

    Shape: ``[<SEVERITY>] <dominant_alert_type> — <alert_count> alerts``
    The severity prefix sits at the front so the calendar UI's
    truncation (Google Calendar shows ~30 chars in the month grid)
    still reveals the severity.
    """
    severity = str(_incident_field(inc, "severity_max", "") or "")
    prefix = _SEVERITY_PREFIX.get(severity.upper(), f"[{severity}]" if severity else "[?]")
    dominant = str(_incident_field(inc, "dominant_alert_type", "") or "incident")
    count = _incident_field(inc, "alert_count", 0)
    try:
        count_i = int(count)
    except (TypeError, ValueError):
        count_i = 0
    plural = "s" if count_i != 1 else ""
    return f"{prefix} {dominant} — {count_i} alert{plural}"


def _incident_description(inc: Any) -> str:
    """Build the DESCRIPTION (body) text for one incident.

    Multi-line plain text. Embeds:

    * the alert_count
    * the dominant_alert_type
    * the entities touched (tickers / routes / ports)
    * a short list of the top alert titles (up to 5)

    Newlines in the OUTPUT stay as ``\\n`` literals after escaping —
    the caller passes the whole DESCRIPTION through ``_escape_ics_text``
    which handles that conversion.
    """
    parts: list[str] = []
    count = _incident_field(inc, "alert_count", 0)
    parts.append(f"Alert count: {count}")
    dominant = _incident_field(inc, "dominant_alert_type", "")
    if dominant:
        parts.append(f"Dominant type: {dominant}")
    severity = _incident_field(inc, "severity_max", "")
    if severity:
        parts.append(f"Severity (worst): {severity}")
    started_at = _incident_field(inc, "started_at", "")
    if started_at:
        parts.append(f"Started: {started_at}")
    entities = _incident_field(inc, "entities_touched", {}) or {}
    if isinstance(entities, dict):
        tickers = entities.get("tickers") or []
        routes = entities.get("routes") or []
        ports = entities.get("ports") or []
        if tickers:
            parts.append(f"Tickers: {', '.join(str(t) for t in tickers[:10])}")
        if routes:
            parts.append(f"Routes: {', '.join(str(r) for r in routes[:10])}")
        if ports:
            parts.append(f"Ports: {', '.join(str(p) for p in ports[:10])}")
    # Top alert titles — up to 5. The ShippingAlert objects expose
    # ``.title``; the dict shape uses key ``title``. ``getattr`` +
    # dict fallback handles both.
    alerts = _incident_field(inc, "alerts", []) or []
    if alerts:
        titles: list[str] = []
        for a in alerts[:5]:
            t = ""
            try:
                if isinstance(a, dict):
                    t = str(a.get("title", "") or "")
                else:
                    t = str(getattr(a, "title", "") or "")
            except Exception:
                t = ""
            if t:
                titles.append(t)
        if titles:
            parts.append("Top alerts:")
            for t in titles:
                parts.append(f"  - {t}")
    return "\n".join(parts)


def _build_vevent(inc: Any, dtstamp: str) -> list[str]:
    """Render one incident as a list of VEVENT content lines.

    The return value is a list of LOGICAL lines (un-folded — folding
    happens once at the end on the whole assembled calendar so we
    don't have to think about it here). Empty / unrenderable
    incidents return an empty list so the caller can ``extend`` over
    a malformed input without crashing.
    """
    try:
        dtstart = _format_ics_datetime(_incident_field(inc, "started_at", ""))
        if not dtstart:
            # No start time → no event. The calendar app would
            # reject a VEVENT without DTSTART anyway.
            return []
        end_raw = _incident_field(inc, "end_at", "") or _incident_field(inc, "ended_at", "")
        dtend = _format_ics_datetime(end_raw)
        if not dtend:
            dtend = _add_hours(dtstart, _DEFAULT_DURATION_HOURS)
        if not dtend:
            # If even the +1h fallback failed (impossible barring a
            # corrupted dtstart), set end == start so the parser
            # treats it as an instantaneous event.
            dtend = dtstart

        uid = _incident_uid(inc)
        summary = _escape_ics_text(_incident_summary(inc))
        description = _escape_ics_text(_incident_description(inc))
        severity = str(_incident_field(inc, "severity_max", "") or "")
        categories = _escape_ics_text(severity) if severity else ""

        lines: list[str] = ["BEGIN:VEVENT"]
        lines.append(f"UID:{uid}")
        lines.append(f"DTSTAMP:{dtstamp}")
        lines.append(f"DTSTART:{dtstart}")
        lines.append(f"DTEND:{dtend}")
        lines.append(f"SUMMARY:{summary}")
        if description:
            lines.append(f"DESCRIPTION:{description}")
        if categories:
            lines.append(f"CATEGORIES:{categories}")
        lines.append("END:VEVENT")
        return lines
    except Exception:
        # NEVER raise from the renderer — skip the offending row.
        return []


# ─────────────────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────────────────

def incidents_to_ics(
    incidents: Iterable[Any],
    *,
    calendar_name: str = "Ship Tracker — Incidents",
    prodid: str = "-//Ship Tracker//Incidents//EN",
) -> str:
    """Render ``incidents`` as a single iCalendar (.ics) string.

    Each incident becomes a VEVENT with the following fields:

      * ``UID``         — ``<incident_id>@shiptracker.local`` (stable
                          across re-fetches so calendar apps update
                          events in place).
      * ``DTSTAMP``     — wall-clock NOW in UTC (per RFC 5545 §3.8.7.2
                          this is the "when was the iCalendar object
                          generated" timestamp, not the event time).
      * ``DTSTART``     — incident.started_at in UTC.
      * ``DTEND``       — incident.end_at if present, else
                          ``DTSTART + 1 hour``.
      * ``SUMMARY``     — ``[<SEVERITY>] <type> — N alerts`` (severity
                          prefix surfaces at the start of the line so
                          truncated calendar grid views still show it).
      * ``DESCRIPTION`` — multi-line body: alert count, dominant type,
                          severity, started_at, entities touched, and
                          up to 5 alert titles.
      * ``CATEGORIES``  — the incident's worst severity. Calendar apps
                          that color-code by category get free
                          severity-based highlighting.

    Output is RFC 5545 compliant:

      * All lines terminated with CRLF (``\\r\\n``).
      * Lines > 75 octets folded per §3.1.
      * Text values escape ``\\``, ``,``, ``;``, and newlines.
      * Wrapped in a single ``VCALENDAR`` block with ``VERSION:2.0``
        and the configured ``PRODID``.

    The function NEVER raises. A bad incident shape (missing
    started_at, severity=None, alerts=not-a-list, etc.) downgrades
    to a less informative event or is skipped entirely rather than
    crashing the renderer. An empty ``incidents`` iterable produces
    a valid empty calendar (``BEGIN:VCALENDAR ... END:VCALENDAR``
    with no VEVENTs).

    Parameters
    ----------
    incidents:
        An iterable of :class:`engine.alert_correlator.AlertIncident`
        instances OR equivalent dicts. Both shapes work — the helper
        accepts either ``incident.severity_max`` or
        ``incident['severity_max']``.
    calendar_name:
        Value for the ``X-WR-CALNAME`` extension property. Calendar
        apps that recognise this property (Google, Apple, Outlook)
        show it as the subscribed-calendar label.
    prodid:
        Value for the mandatory ``PRODID`` property. The RFC 5545
        convention is ``-//<organization>//<product>//<lang>``.
    """
    try:
        dtstamp = _now_utc_ics()
        header: list[str] = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            f"PRODID:{prodid}",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
            f"X-WR-CALNAME:{_escape_ics_text(calendar_name)}",
            f"X-WR-CALDESC:{_escape_ics_text('Shipping incidents from Ship Tracker.')}",
        ]
        body: list[str] = []
        if incidents:
            for inc in incidents:
                body.extend(_build_vevent(inc, dtstamp))
        footer: list[str] = ["END:VCALENDAR"]
        # Join with CRLF; fold AFTER join so the folder sees the
        # canonical line layout it expects.
        raw = _CRLF.join(header + body + footer)
        folded = _fold_long_lines(raw)
        # Per RFC 5545 §3.1 every line — INCLUDING the last — is
        # terminated with CRLF. The join above puts CRLF BETWEEN
        # lines; we add one trailing CRLF here to terminate the
        # final ``END:VCALENDAR``.
        if not folded.endswith(_CRLF):
            folded = folded + _CRLF
        return folded
    except Exception:
        # Defensive fallback — emit a valid empty calendar instead
        # of a 500 trace. The ICS endpoint is public-fetched by
        # calendar apps; we must never let render errors propagate
        # to the wire.
        return (
            "BEGIN:VCALENDAR" + _CRLF
            + "VERSION:2.0" + _CRLF
            + f"PRODID:{prodid}" + _CRLF
            + "CALSCALE:GREGORIAN" + _CRLF
            + "METHOD:PUBLISH" + _CRLF
            + "END:VCALENDAR" + _CRLF
        )


__all__ = [
    "incidents_to_ics",
    # Helpers exposed for unit testing — not for general consumption.
    "_escape_ics_text",
    "_fold_long_lines",
    "_format_ics_datetime",
]
