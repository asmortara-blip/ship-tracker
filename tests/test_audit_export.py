"""Tests for ``utils.audit_export`` — JSONL formatter + DB-backed exporters.

Defining properties under test:

* ``rows_to_jsonl`` produces one JSON object per line, ``\\n``-terminated,
  with a trailing newline on the last line, and zero bytes for an empty
  input. SIEM scrapers depend on this exact shape.
* Each line is independently ``json.loads``-able — that's the whole
  point of JSONL vs. an array envelope.
* Non-JSON-native values (datetimes, paths) degrade to strings via
  ``default=str`` rather than raising. Matches the convention used by
  ``record_audit``.
* UTF-8 is preserved (no ``\\uXXXX`` escape blow-up).
* The DB-backed wrappers honour every filter — ``user_id``, ``action``,
  ``since`` / ``until``, ``limit`` — and the streaming variant chunks
  via multiple ``write`` calls so the OS doesn't see one giant buffer.
"""
from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone

import pytest


# ─── Isolation fixture ────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Per-test SQLite. Mirrors the standard fixture used elsewhere in
    the suite so audit writes land in tmp, not in cache/."""
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Helpers ──────────────────────────────────────────────────────────────

def _seed_audit(action: str, *, user_id: str = "u-1", detail: dict | None = None) -> None:
    """Plant one audit row through the real record_audit path so the
    full ``query_audit`` round-trip is exercised."""
    from auth.audit import record_audit
    record_audit(action, user_id=user_id, detail=detail or {})


# ─── rows_to_jsonl — pure formatter ───────────────────────────────────────

def test_rows_to_jsonl_empty_returns_empty_bytes():
    """An empty input must return ``b""`` (zero-byte file) — NOT
    ``b"\\n"``. SIEM parsers treat a blank line as a parse error."""
    from utils.audit_export import rows_to_jsonl
    assert rows_to_jsonl([]) == b""


def test_rows_to_jsonl_three_rows_each_parseable_as_json():
    """Three input rows → three JSONL lines, each independently
    parseable. The whole point of JSONL is line-level granularity."""
    from utils.audit_export import rows_to_jsonl
    rows = [
        {"event_id": "e1", "action": "login_success", "user_id": "u-1"},
        {"event_id": "e2", "action": "save_rules",    "user_id": "u-1"},
        {"event_id": "e3", "action": "delete_report", "user_id": "u-1"},
    ]
    out = rows_to_jsonl(rows)
    lines = out.decode("utf-8").rstrip("\n").split("\n")
    assert len(lines) == 3
    parsed = [json.loads(ln) for ln in lines]
    assert [p["event_id"] for p in parsed] == ["e1", "e2", "e3"]


def test_rows_to_jsonl_ends_with_trailing_newline():
    """The final line MUST be \\n-terminated so a concatenation of
    multiple exports remains valid JSONL — without the trailing
    newline the last row of file A would smash into the first row of
    file B."""
    from utils.audit_export import rows_to_jsonl
    out = rows_to_jsonl([{"k": "v"}])
    assert out.endswith(b"\n")


def test_rows_to_jsonl_handles_non_serializable_values_via_str():
    """Datetimes and other non-JSON types must fall back to str().
    Matches the convention used by record_audit + the HTTP API."""
    from utils.audit_export import rows_to_jsonl
    ts = datetime(2026, 5, 23, 12, 0, 0, tzinfo=timezone.utc)
    out = rows_to_jsonl([{"event_id": "e1", "when": ts}])
    line = out.decode("utf-8").rstrip("\n")
    parsed = json.loads(line)
    assert parsed["event_id"] == "e1"
    # default=str → datetime renders as its isoformat-ish repr; we
    # only assert it's a STRING, not the exact form, because Python
    # versions differ slightly in their datetime repr.
    assert isinstance(parsed["when"], str)
    assert "2026" in parsed["when"]


def test_rows_to_jsonl_preserves_utf8():
    """UTF-8 characters in payloads (Asian-script titles, emoji)
    must come through intact, NOT escape-encoded as \\uXXXX. The
    JSONL is meant to be human-readable and SIEM-parseable both."""
    from utils.audit_export import rows_to_jsonl
    out = rows_to_jsonl([
        {"action": "save_rules", "detail_json": {"title": "貨物 🚢"}},
    ])
    text = out.decode("utf-8")
    assert "貨物" in text
    assert "🚢" in text
    # Round-trip parses back to the same payload.
    parsed = json.loads(text.rstrip("\n"))
    assert parsed["detail_json"]["title"] == "貨物 🚢"


# ─── export_audit_to_jsonl — end-to-end via the real DB ───────────────────

def test_export_audit_to_jsonl_happy_path():
    """One seeded row → one JSONL line carrying the same action verb.
    Exercises the full ``record_audit → query_audit → rows_to_jsonl``
    pipeline."""
    from utils.audit_export import export_audit_to_jsonl
    _seed_audit("login_success", user_id="u-1")
    body = export_audit_to_jsonl(user_id="u-1")
    assert body.endswith(b"\n")
    lines = body.decode("utf-8").rstrip("\n").split("\n")
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["action"] == "login_success"
    assert parsed["user_id"] == "u-1"


def test_export_audit_to_jsonl_filters_by_user_id():
    """Per-user scoping: passing ``user_id=u-A`` must not surface
    u-B's rows. This is the core safety property the SIEM operator
    relies on when running per-tenant scrapers."""
    from utils.audit_export import export_audit_to_jsonl
    _seed_audit("login_success", user_id="u-A")
    _seed_audit("login_success", user_id="u-B")
    body = export_audit_to_jsonl(user_id="u-A")
    lines = body.decode("utf-8").rstrip("\n").split("\n")
    parsed = [json.loads(ln) for ln in lines]
    assert all(p["user_id"] == "u-A" for p in parsed)


def test_export_audit_to_jsonl_filters_by_action():
    """``action="save_rules"`` filter must drop other verbs even when
    the user has many."""
    from utils.audit_export import export_audit_to_jsonl
    _seed_audit("login_success", user_id="u-1")
    _seed_audit("save_rules", user_id="u-1")
    _seed_audit("delete_report", user_id="u-1")
    body = export_audit_to_jsonl(user_id="u-1", action="save_rules")
    lines = body.decode("utf-8").rstrip("\n").split("\n")
    parsed = [json.loads(ln) for ln in lines]
    assert len(parsed) == 1
    assert parsed[0]["action"] == "save_rules"


def test_export_audit_to_jsonl_with_since_until_window():
    """``since`` / ``until`` form a half-open window on
    ``created_at``. We can't trivially backdate the recorded rows
    (record_audit stamps NOW internally), so we use a since/until
    pair that BRACKETS the current moment and confirm rows fall
    inside / outside as expected.

    Strategy:
      * since = one minute ago → present-day rows are included.
      * until = one minute ago → present-day rows are excluded.
    """
    from utils.audit_export import export_audit_to_jsonl
    _seed_audit("ut_window_marker", user_id="u-1")

    now = datetime.now(timezone.utc)
    past = (now - timedelta(minutes=1)).isoformat()
    # since=past → row should be included
    body_in = export_audit_to_jsonl(user_id="u-1", since=past)
    assert b"ut_window_marker" in body_in
    # until=past → row should be excluded (created AFTER past)
    body_out = export_audit_to_jsonl(user_id="u-1", until=past)
    assert b"ut_window_marker" not in body_out


def test_export_audit_to_jsonl_with_limit_cap():
    """``limit=3`` must cap the JSONL line count at 3 even when more
    rows match."""
    from utils.audit_export import export_audit_to_jsonl
    for i in range(10):
        _seed_audit("looped", user_id="u-1", detail={"i": i})
    body = export_audit_to_jsonl(user_id="u-1", limit=3)
    lines = body.decode("utf-8").rstrip("\n").split("\n")
    assert len(lines) == 3


# ─── export_audit_to_stream — chunked writer ──────────────────────────────

def test_export_audit_to_stream_chunks_via_multiple_writes():
    """5000-row export with batch_size=1000 → at least 5 writes to
    the stream. Proves the streaming path isn't accidentally falling
    back to a single buffer (which would defeat the whole point)."""
    from utils.audit_export import export_audit_to_stream

    for i in range(5000):
        _seed_audit("stream_test", user_id="u-1", detail={"i": i})

    class CountingStream:
        # Setting mode='wb' marks this stream as binary so the
        # exporter writes raw bytes rather than decoded strings —
        # matches the detection contract in _stream_is_binary.
        mode = "wb"

        def __init__(self):
            self.write_count = 0
            self.buf = bytearray()

        def write(self, data):
            self.write_count += 1
            if isinstance(data, str):
                self.buf.extend(data.encode("utf-8"))
            else:
                self.buf.extend(data)
            return len(data)

        def flush(self):
            pass

    stream = CountingStream()

    n = export_audit_to_stream(
        stream, batch_size=1000, user_id="u-1", limit=5000,
    )
    assert n == 5000
    # batch_size=1000 over 5000 rows → 5 batches → 5 writes.
    assert stream.write_count >= 5, (
        f"expected at least 5 chunked writes, got {stream.write_count}"
    )
    # Total bytes match a JSONL of 5000 rows (each ends in \n).
    assert stream.buf.decode("utf-8").count("\n") == 5000


def test_export_audit_to_stream_never_raises():
    """Even when the underlying query_audit fails (e.g. broken DB),
    the stream variant must return 0 rather than propagating —
    contract mirrors record_audit / query_audit themselves."""
    from utils.audit_export import export_audit_to_stream

    # No seeded rows + a sane stream → 0 returned cleanly.
    stream = io.BytesIO()
    n = export_audit_to_stream(stream, user_id="u-1")
    assert n == 0
    # And the stream is empty (no spurious bytes from an empty
    # result).
    assert stream.getvalue() == b""

    # Now also exercise a broken stream — write raises mid-flight.
    _seed_audit("broken_stream_test", user_id="u-1")

    class BrokenStream:
        mode = "wb"  # binary

        def write(self, _data):
            raise OSError("disk full")

        def flush(self):
            pass

    broken = BrokenStream()
    # Must NOT raise — returns the count of rows written (0 since
    # the first write threw).
    n2 = export_audit_to_stream(broken, user_id="u-1")
    assert n2 == 0
