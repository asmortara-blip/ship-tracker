"""Tests for ``utils.ics_export`` — RFC 5545 iCalendar rendering of
correlated alert-incidents.

Defining properties under test:

* ``incidents_to_ics`` NEVER raises — empty input, garbage incident
  shapes, None severity, missing started_at all degrade gracefully.
* Output is structurally valid: ``BEGIN:VCALENDAR`` ... ``END:VCALENDAR``,
  every VEVENT has DTSTART / DTSTAMP / UID / SUMMARY.
* SUMMARY carries a severity prefix at the start so calendar-grid
  truncation doesn't hide it.
* RFC 5545 wire-format rules honoured: CRLF line endings, ≤75-octet
  line folding, escape of backslash / comma / semicolon / newlines.
* DTSTART is UTC in the form ``YYYYMMDDTHHMMSSZ``.
* Unicode survives round-trip (Japanese port names, emoji, etc.).
* The escape helper is reversal-safe — what we escape we can
  un-escape.

We do NOT depend on the optional ``icalendar`` package. The tests
fall back to structural assertions; if ``icalendar`` is importable
locally we add a parse-round-trip sanity check.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest


# ─── Lightweight incident stub ────────────────────────────────────────────


@dataclass
class _StubIncident:
    """Minimal AlertIncident-compatible dataclass for tests.

    Mirrors the public fields the renderer reads. Keeps the test
    file independent of the real engine module so changes to
    AlertIncident don't break the rendering contract these tests
    pin (and so we don't have to spin up the alert DB to render
    one event)."""
    incident_id: str = "inc-0001"
    started_at: str = "2026-05-22T14:30:00Z"
    severity_max: str = "HIGH"
    alert_count: int = 3
    dominant_alert_type: str = "BDI_MOVE"
    alerts: list = field(default_factory=list)
    entities_touched: dict = field(
        default_factory=lambda: {"tickers": ["ZIM"], "routes": [], "ports": []}
    )


# ─── _escape_ics_text ────────────────────────────────────────────────────


def test_escape_text_handles_basic_specials() -> None:
    """RFC 5545 §3.3.11 — backslash, semicolon, comma, newline escaped."""
    from utils.ics_export import _escape_ics_text

    out = _escape_ics_text("a;b,c\\d\ne")
    # Backslash first → doubles, then ;,\n become \;\\,\\n
    assert "\\;" in out
    assert "\\," in out
    assert "\\\\" in out
    # Literal newline becomes the two-character escape \n.
    assert "\\n" in out
    # No raw newline survives.
    assert "\n" not in out


def test_escape_text_empty_or_non_string_returns_empty() -> None:
    """Non-string inputs (None, int, dict) become empty strings —
    keeps the renderer NEVER-raises contract."""
    from utils.ics_export import _escape_ics_text

    assert _escape_ics_text("") == ""
    assert _escape_ics_text(None) == ""  # type: ignore[arg-type]
    assert _escape_ics_text(42) == ""  # type: ignore[arg-type]
    assert _escape_ics_text({"a": 1}) == ""  # type: ignore[arg-type]


def test_escape_text_is_round_trip_safe() -> None:
    """What we escape we can un-escape — sanity-check the inverse
    mapping. RFC 5545 doesn't ship a stdlib un-escape; we implement
    the inverse here as the contract the renderer effectively
    promises."""
    from utils.ics_export import _escape_ics_text

    src = "trade route ZIM/2M; cargo, vol\\=12k\nmsg"
    enc = _escape_ics_text(src)
    # Manual inverse — order matters: \\n first (so literal-n in
    # the input doesn't get mistaken for the newline escape),
    # then , ; \\.
    dec = enc.replace("\\n", "\n").replace("\\,", ",")
    dec = dec.replace("\\;", ";").replace("\\\\", "\\")
    assert dec == src


# ─── _fold_long_lines ────────────────────────────────────────────────────


def test_fold_short_line_passes_through() -> None:
    """Lines ≤75 octets are unchanged."""
    from utils.ics_export import _fold_long_lines

    line = "SUMMARY:short"
    out = _fold_long_lines(line)
    assert out == line


def test_fold_long_line_wraps_with_crlf_and_space() -> None:
    """RFC 5545 §3.1 — long lines wrap with CRLF + single SPACE
    continuation."""
    from utils.ics_export import _fold_long_lines

    long = "DESCRIPTION:" + ("x" * 200)
    out = _fold_long_lines(long)
    assert "\r\n " in out
    # No single physical line exceeds 75 octets.
    for line in out.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75


def test_fold_preserves_utf8_codepoint_boundaries() -> None:
    """Splits must NOT land in the middle of a multibyte UTF-8 sequence."""
    from utils.ics_export import _fold_long_lines

    # 50 Japanese characters → ~150 octets, will need folding.
    line = "DESCRIPTION:" + ("東京" * 50)
    out = _fold_long_lines(line)
    # The output must still decode round-trip as UTF-8 (i.e. no
    # broken characters introduced by the fold).
    rejoined = out.replace("\r\n ", "")
    assert rejoined == line


# ─── _format_ics_datetime ────────────────────────────────────────────────


def test_format_datetime_iso_z_string() -> None:
    """Z-suffixed ISO 8601 → YYYYMMDDTHHMMSSZ."""
    from utils.ics_export import _format_ics_datetime

    assert _format_ics_datetime("2026-05-22T14:30:00Z") == "20260522T143000Z"


def test_format_datetime_garbage_returns_empty() -> None:
    """Malformed input → empty string, never an exception."""
    from utils.ics_export import _format_ics_datetime

    assert _format_ics_datetime("not a date") == ""
    assert _format_ics_datetime(None) == ""
    assert _format_ics_datetime({"x": 1}) == ""  # type: ignore[arg-type]


# ─── incidents_to_ics — structural contract ───────────────────────────────


def test_empty_list_returns_valid_empty_calendar() -> None:
    """No incidents → still a valid VCALENDAR shell with no VEVENTs."""
    from utils.ics_export import incidents_to_ics

    ics = incidents_to_ics([])
    assert ics.startswith("BEGIN:VCALENDAR")
    assert ics.rstrip("\r\n").endswith("END:VCALENDAR")
    assert "VERSION:2.0" in ics
    assert "BEGIN:VEVENT" not in ics


def test_one_incident_produces_vevent_block() -> None:
    """One incident → one VEVENT with required fields present."""
    from utils.ics_export import incidents_to_ics

    inc = _StubIncident()
    ics = incidents_to_ics([inc])
    assert "BEGIN:VEVENT" in ics
    assert "END:VEVENT" in ics
    # Required RFC 5545 §3.6.1 fields.
    assert "UID:" in ics
    assert "DTSTAMP:" in ics
    assert "DTSTART:" in ics
    assert "SUMMARY:" in ics


def test_summary_carries_severity_prefix() -> None:
    """SUMMARY must start with the severity in brackets so a
    truncated calendar-grid view still reveals it."""
    from utils.ics_export import incidents_to_ics

    inc = _StubIncident(severity_max="CRITICAL")
    ics = incidents_to_ics([inc])
    # Find the SUMMARY line in the (possibly folded) output.
    summary_line = next(
        l for l in ics.split("\r\n") if l.startswith("SUMMARY:")
    )
    assert "[CRITICAL]" in summary_line


def test_long_description_gets_folded() -> None:
    """A long description forces line folding — no physical line in
    the output exceeds 75 octets after folding."""
    from utils.ics_export import incidents_to_ics

    long_title = "x" * 400
    inc = _StubIncident(
        alerts=[type("A", (), {"title": long_title})() for _ in range(3)]
    )
    ics = incidents_to_ics([inc])
    for line in ics.split("\r\n"):
        # Allow the empty-string between lines from the strip.
        assert len(line.encode("utf-8")) <= 75


def test_special_chars_in_description_are_escaped() -> None:
    """Commas / semicolons / backslashes inside the description
    survive escaping — RFC 5545 §3.3.11."""
    from utils.ics_export import incidents_to_ics

    msg_title = "ZIM, MAERSK; ratio=4\\3 escalation"

    class _Alert:
        title = msg_title

    inc = _StubIncident(alerts=[_Alert()])
    ics = incidents_to_ics([inc])
    # The DESCRIPTION line(s) must contain the escaped forms.
    desc_blob = "".join(
        line for line in ics.split("\r\n")
        if line.startswith("DESCRIPTION:") or line.startswith(" ")
    )
    # After fold-join the escapes are still present.
    assert "\\," in desc_blob
    assert "\\;" in desc_blob
    assert "\\\\" in desc_blob


def test_unicode_survives_roundtrip() -> None:
    """Japanese / emoji / Cyrillic survives — folding doesn't slice
    codepoints, encoding stays UTF-8 throughout."""
    from utils.ics_export import incidents_to_ics

    title = "東京港 → ロサンゼルス 🚢 congestion"

    class _Alert:
        title = "東京港 → ロサンゼルス 🚢 congestion"

    inc = _StubIncident(
        dominant_alert_type="CONGESTION",
        alerts=[_Alert()],
        entities_touched={"tickers": [], "routes": [], "ports": ["JPTYO"]},
    )
    ics = incidents_to_ics([inc])
    # The raw text after un-folding should contain the unicode.
    unfolded = ics.replace("\r\n ", "")
    assert "東京港" in unfolded
    assert "🚢" in unfolded


def test_output_uses_crlf_line_endings() -> None:
    """RFC 5545 §3.1 — content lines terminated with CRLF, not bare LF."""
    from utils.ics_export import incidents_to_ics

    ics = incidents_to_ics([_StubIncident()])
    # If we strip CRLF the result has NO bare LFs (every \n is in
    # a \r\n pair).
    assert ics.count("\n") == ics.count("\r\n")


def test_every_vevent_has_categories_with_severity() -> None:
    """The incident's severity_max gets emitted as a CATEGORIES line
    so calendar apps that color-code by category can highlight."""
    from utils.ics_export import incidents_to_ics

    ics = incidents_to_ics([_StubIncident(severity_max="HIGH")])
    assert "CATEGORIES:HIGH" in ics


def test_dtstart_format_is_basic_utc() -> None:
    """DTSTART is the RFC 5545 form #2 UTC: YYYYMMDDTHHMMSSZ — no
    dashes, no colons, trailing Z."""
    from utils.ics_export import incidents_to_ics

    ics = incidents_to_ics([_StubIncident()])
    dtstart_line = next(
        l for l in ics.split("\r\n") if l.startswith("DTSTART:")
    )
    value = dtstart_line.split(":", 1)[1]
    # Exact form: 15 chars, with T at position 8, Z at position 14.
    assert len(value) == 16
    assert value[8] == "T"
    assert value[-1] == "Z"
    assert value[:8].isdigit()


def test_never_raises_on_bad_incident_shape() -> None:
    """A degenerate incident (None severity, missing started_at, etc.)
    must NOT crash the renderer — that would 500 the public endpoint."""
    from utils.ics_export import incidents_to_ics

    bogus = [
        # Missing all fields entirely.
        type("X", (), {})(),
        # Wrong types.
        type("X", (), {"started_at": 12345, "severity_max": None,
                       "alert_count": "many", "alerts": "not-a-list",
                       "entities_touched": "nope"})(),
        # Empty.
        _StubIncident(started_at="", severity_max="", alert_count=0,
                      alerts=[], entities_touched={}),
    ]
    # Should not raise.
    ics = incidents_to_ics(bogus)
    assert ics.startswith("BEGIN:VCALENDAR")
    assert ics.rstrip("\r\n").endswith("END:VCALENDAR")


def test_dict_input_works_alongside_dataclass() -> None:
    """The renderer accepts dict-shaped incidents too — useful when
    callers have already serialized via vars() / asdict()."""
    from utils.ics_export import incidents_to_ics

    inc_dict = {
        "incident_id": "inc-dict",
        "started_at": "2026-05-22T10:00:00Z",
        "severity_max": "MEDIUM",
        "alert_count": 2,
        "dominant_alert_type": "STOCK_MOVE",
        "alerts": [],
        "entities_touched": {"tickers": ["MAERSK"], "routes": [], "ports": []},
    }
    ics = incidents_to_ics([inc_dict])
    assert "BEGIN:VEVENT" in ics
    assert "[MEDIUM]" in ics
    assert "STOCK_MOVE" in ics


# ─── Optional: parse with the `icalendar` package if available ────────────


def test_parses_with_icalendar_package_if_installed() -> None:
    """If the optional ``icalendar`` package is locally importable,
    confirm the rendered output round-trips through a real parser.
    Skipped when the package isn't installed (the module has no
    third-party dependency — this is a bonus assertion only)."""
    icalendar = pytest.importorskip("icalendar")
    from utils.ics_export import incidents_to_ics

    ics = incidents_to_ics([_StubIncident()])
    cal = icalendar.Calendar.from_ical(ics)
    # Single VEVENT, with the four required fields populated.
    events = [c for c in cal.walk("VEVENT")]
    assert len(events) == 1
    ev = events[0]
    assert ev.get("uid")
    assert ev.get("dtstamp")
    assert ev.get("dtstart")
    assert ev.get("summary")
