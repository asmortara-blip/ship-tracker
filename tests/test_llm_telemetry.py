"""Tests for engine.llm_telemetry — record + summarize Anthropic API calls.

Coverage:
  - record_call inserts a row with the right shape; auto-generates
    call_id (uuid) and created_at; returns None
  - record_call computes est_cost_usd correctly for known models
    (haiku $1/$5 per MTok, sonnet $3/$15, opus $15/$75)
  - record_call defaults unknown models to haiku rates with a debug log
  - record_call never raises — DB writes are best-effort
  - get_usage_summary on empty DB returns zeroed dict with right keys
  - get_usage_summary aggregates 3 calls across 2 sources correctly,
    including by_source / by_model breakdowns
  - get_usage_summary window_days filter excludes calls older than N days
  - by_day is sorted ascending by date
  - get_recent_calls respects ordering (newest first) and limit

Every test is hermetic — no real Anthropic SDK calls, per-test SQLite
isolation via the standard monkeypatch idiom from test_state_db.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest


# ─── DB isolation: every test gets a per-test SQLite file ─────────────────

@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """Every test gets a fresh DB pointed at tmp_path. Critical because
    llm_calls is a shared state table — without isolation, tests would
    see each other's rows."""
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Helpers ──────────────────────────────────────────────────────────────

def _all_rows():
    """Return every row of llm_calls as a list of sqlite3.Row objects."""
    from state.db import get_connection
    conn = get_connection()
    return conn.execute(
        "SELECT * FROM llm_calls ORDER BY created_at ASC"
    ).fetchall()


def _backdate_latest(days: int) -> str:
    """Shift the most recent llm_calls row's created_at backwards by N days.

    Returns the new ISO timestamp written to the row. Used by window-filter
    tests to simulate older calls without forging UUIDs by hand."""
    from state.db import get_connection
    conn = get_connection()
    new_ts = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn.execute(
        """
        UPDATE llm_calls
        SET created_at = ?
        WHERE call_id = (
            SELECT call_id FROM llm_calls ORDER BY created_at DESC LIMIT 1
        )
        """,
        (new_ts,),
    )
    return new_ts


# ═══════════════════════════════════════════════════════════════════════════
# record_call — shape, auto-generated fields
# ═══════════════════════════════════════════════════════════════════════════

def test_record_call_inserts_row_with_correct_shape() -> None:
    """record_call writes one row with the expected column values."""
    from engine.llm_telemetry import record_call

    record_call(
        source="commentary",
        model="claude-haiku-4-5-20251001",
        tokens_in=100,
        tokens_out=50,
        cached_tokens=20,
        tab_name="overview",
    )

    rows = _all_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "commentary"
    assert row["tab_name"] == "overview"
    assert row["model"] == "claude-haiku-4-5-20251001"
    assert row["tokens_in"] == 100
    assert row["tokens_out"] == 50
    assert row["cached_tokens"] == 20


def test_record_call_auto_generates_call_id_as_uuid() -> None:
    """call_id is a valid uuid4 string — caller doesn't supply one."""
    from engine.llm_telemetry import record_call

    record_call(
        source="narration",
        model="claude-haiku-4-5-20251001",
        tokens_in=1, tokens_out=1,
    )

    rows = _all_rows()
    assert len(rows) == 1
    # If this raises, the call_id is not a well-formed UUID.
    parsed = uuid.UUID(rows[0]["call_id"])
    assert str(parsed) == rows[0]["call_id"]


def test_record_call_auto_generates_iso_created_at() -> None:
    """created_at is set to ISO 8601 UTC at insertion time."""
    from engine.llm_telemetry import record_call

    before = datetime.now(timezone.utc)
    record_call(source="commentary", model="claude-haiku-4-5-20251001",
                tokens_in=1, tokens_out=1)
    after = datetime.now(timezone.utc)

    rows = _all_rows()
    assert len(rows) == 1
    ts = datetime.fromisoformat(rows[0]["created_at"])
    # Tolerate microsecond drift between Python's `now()` calls.
    assert before - timedelta(seconds=1) <= ts <= after + timedelta(seconds=1)


def test_record_call_returns_none() -> None:
    """record_call is a side-effect-only API — explicit return is None."""
    from engine.llm_telemetry import record_call

    out = record_call(
        source="commentary", model="claude-haiku-4-5-20251001",
        tokens_in=1, tokens_out=1,
    )
    assert out is None


def test_record_call_defaults_tab_name_and_cached_tokens() -> None:
    """Optional kwargs default to empty string / 0."""
    from engine.llm_telemetry import record_call

    record_call(
        source="narration",
        model="claude-haiku-4-5-20251001",
        tokens_in=10, tokens_out=5,
    )
    rows = _all_rows()
    assert rows[0]["tab_name"] == ""
    assert rows[0]["cached_tokens"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# record_call — cost estimation
# ═══════════════════════════════════════════════════════════════════════════

def test_cost_estimate_haiku_rates() -> None:
    """Haiku 4.5: $1/MTok input + $5/MTok output. 1M in + 1M out = $6.00."""
    from engine.llm_telemetry import record_call

    record_call(
        source="commentary",
        model="claude-haiku-4-5-20251001",
        tokens_in=1_000_000,
        tokens_out=1_000_000,
    )
    rows = _all_rows()
    # 1M * $1 + 1M * $5 = $6.00
    assert rows[0]["est_cost_usd"] == pytest.approx(6.0, abs=1e-6)


def test_cost_estimate_sonnet_rates() -> None:
    """Sonnet 4.6: $3/MTok input + $15/MTok output. 1M in + 1M out = $18.00."""
    from engine.llm_telemetry import record_call

    record_call(
        source="commentary",
        model="claude-sonnet-4-6",
        tokens_in=1_000_000,
        tokens_out=1_000_000,
    )
    rows = _all_rows()
    assert rows[0]["est_cost_usd"] == pytest.approx(18.0, abs=1e-6)


def test_cost_estimate_opus_rates() -> None:
    """Opus 4.7: $15/MTok input + $75/MTok output. 1M in + 1M out = $90.00."""
    from engine.llm_telemetry import record_call

    record_call(
        source="commentary",
        model="claude-opus-4-7",
        tokens_in=1_000_000,
        tokens_out=1_000_000,
    )
    rows = _all_rows()
    assert rows[0]["est_cost_usd"] == pytest.approx(90.0, abs=1e-6)


def test_cost_estimate_small_call_haiku() -> None:
    """A small Haiku call should resolve to a fractional-cent figure
    with enough precision to be non-zero — verifies the 6-decimal
    rounding doesn't truncate cheap calls to zero."""
    from engine.llm_telemetry import record_call

    # 500 in + 200 out at Haiku rates:
    # 500/1e6 * $1 + 200/1e6 * $5 = 0.0005 + 0.0010 = 0.0015
    record_call(
        source="commentary",
        model="claude-haiku-4-5-20251001",
        tokens_in=500,
        tokens_out=200,
    )
    rows = _all_rows()
    assert rows[0]["est_cost_usd"] == pytest.approx(0.0015, abs=1e-6)


def test_unknown_model_defaults_to_haiku_rates(caplog) -> None:
    """An unrecognised model ID falls back to Haiku rates with a debug
    log line, never raises, and produces a non-zero cost estimate."""
    from engine.llm_telemetry import record_call

    record_call(
        source="commentary",
        model="claude-FUTURE-MODEL-NOT-IN-TABLE",
        tokens_in=1_000_000,
        tokens_out=1_000_000,
    )

    rows = _all_rows()
    assert len(rows) == 1
    # Falls back to Haiku rates: 1M * $1 + 1M * $5 = $6.00
    assert rows[0]["est_cost_usd"] == pytest.approx(6.0, abs=1e-6)
    # Original (unknown) model ID is still stored as-is for forensics.
    assert rows[0]["model"] == "claude-FUTURE-MODEL-NOT-IN-TABLE"


def test_record_call_swallows_db_errors(monkeypatch) -> None:
    """record_call must never raise — a broken DB connection is logged
    at debug and the call returns normally. Telemetry failure cannot
    propagate into the engine's hot path."""
    from engine import llm_telemetry as telem

    def _broken_get_connection():
        raise RuntimeError("database is locked")

    # Replace the DB accessor with one that always raises.
    monkeypatch.setattr(
        "state.db.get_connection", _broken_get_connection,
    )

    # MUST NOT raise.
    telem.record_call(
        source="commentary", model="claude-haiku-4-5-20251001",
        tokens_in=1, tokens_out=1,
    )


# ═══════════════════════════════════════════════════════════════════════════
# get_usage_summary
# ═══════════════════════════════════════════════════════════════════════════

def test_summary_empty_db_returns_zeroed_dict() -> None:
    """No calls recorded → all totals zero, all breakdowns empty."""
    from engine.llm_telemetry import get_usage_summary

    s = get_usage_summary(window_days=7)
    assert s["window_days"] == 7
    assert s["total_calls"] == 0
    assert s["total_tokens_in"] == 0
    assert s["total_tokens_out"] == 0
    assert s["total_cost_usd"] == 0.0
    assert s["by_source"] == {}
    assert s["by_model"] == {}
    assert s["by_day"] == []


def test_summary_has_all_expected_keys() -> None:
    """Lock the public shape of the summary dict so UI code can rely on
    keys being present even on an empty DB."""
    from engine.llm_telemetry import get_usage_summary

    s = get_usage_summary(window_days=7)
    expected_keys = {
        "window_days", "total_calls", "total_tokens_in", "total_tokens_out",
        "total_cost_usd", "by_source", "by_model", "by_day",
    }
    assert set(s.keys()) == expected_keys


def test_summary_aggregates_three_calls_across_two_sources() -> None:
    """Three calls — 2 commentary + 1 narration — sum correctly in
    totals, by_source, and by_model breakdowns."""
    from engine.llm_telemetry import get_usage_summary, record_call

    # 2 commentary calls (Haiku) + 1 narration call (Sonnet)
    record_call(source="commentary", model="claude-haiku-4-5-20251001",
                tokens_in=100, tokens_out=50, tab_name="overview")
    record_call(source="commentary", model="claude-haiku-4-5-20251001",
                tokens_in=200, tokens_out=80, tab_name="portfolio")
    record_call(source="narration",  model="claude-sonnet-4-6",
                tokens_in=500, tokens_out=300)

    s = get_usage_summary(window_days=7)
    assert s["total_calls"] == 3
    assert s["total_tokens_in"] == 100 + 200 + 500
    assert s["total_tokens_out"] == 50 + 80 + 300

    # Cost check (per-MTok):
    # commentary: (300 * $1 + 130 * $5) / 1e6 = (300 + 650) / 1e6 = 0.00095
    # narration:  (500 * $3 + 300 * $15) / 1e6 = (1500 + 4500) / 1e6 = 0.006
    expected_total = round((300 * 1 + 130 * 5 + 500 * 3 + 300 * 15) / 1_000_000.0, 6)
    assert s["total_cost_usd"] == pytest.approx(expected_total, abs=1e-6)

    # by_source breakdown
    assert "commentary" in s["by_source"]
    assert "narration" in s["by_source"]
    assert s["by_source"]["commentary"]["calls"] == 2
    assert s["by_source"]["commentary"]["tokens_in"] == 300
    assert s["by_source"]["commentary"]["tokens_out"] == 130
    assert s["by_source"]["narration"]["calls"] == 1
    assert s["by_source"]["narration"]["tokens_in"] == 500
    assert s["by_source"]["narration"]["tokens_out"] == 300

    # by_model breakdown
    assert "claude-haiku-4-5-20251001" in s["by_model"]
    assert "claude-sonnet-4-6" in s["by_model"]
    assert s["by_model"]["claude-haiku-4-5-20251001"]["calls"] == 2
    assert s["by_model"]["claude-sonnet-4-6"]["calls"] == 1


def test_summary_window_days_excludes_older_calls() -> None:
    """A call backdated 10 days must be excluded from a 7-day window."""
    from engine.llm_telemetry import get_usage_summary, record_call

    # Recent call — kept.
    record_call(source="commentary", model="claude-haiku-4-5-20251001",
                tokens_in=100, tokens_out=50)
    # "Old" call — backdated past the 7-day window.
    record_call(source="commentary", model="claude-haiku-4-5-20251001",
                tokens_in=999, tokens_out=999)
    _backdate_latest(days=10)

    s = get_usage_summary(window_days=7)
    assert s["total_calls"] == 1
    assert s["total_tokens_in"] == 100
    assert s["total_tokens_out"] == 50

    # Widening the window to 30 days picks up the older row.
    s30 = get_usage_summary(window_days=30)
    assert s30["total_calls"] == 2
    assert s30["total_tokens_in"] == 100 + 999


def test_summary_by_day_sorted_ascending() -> None:
    """by_day rows must be sorted ascending by date — chart code
    downstream depends on this monotonic order to draw the timeline
    left-to-right without re-sorting."""
    from engine.llm_telemetry import get_usage_summary, record_call

    # Three calls across three days (today, -1, -2). Insert recent first;
    # the SQL ORDER BY day ASC must put oldest first regardless.
    record_call(source="commentary", model="claude-haiku-4-5-20251001",
                tokens_in=1, tokens_out=1)
    record_call(source="commentary", model="claude-haiku-4-5-20251001",
                tokens_in=1, tokens_out=1)
    _backdate_latest(days=1)
    record_call(source="commentary", model="claude-haiku-4-5-20251001",
                tokens_in=1, tokens_out=1)
    _backdate_latest(days=2)

    s = get_usage_summary(window_days=7)
    dates = [r["date"] for r in s["by_day"]]
    assert dates == sorted(dates)
    assert len(dates) == 3
    # Each row carries a calls count > 0 — confirms no gap-filling padding.
    for row in s["by_day"]:
        assert row["calls"] > 0


def test_summary_by_day_includes_only_days_with_calls() -> None:
    """If a day has zero calls, no row appears for it in by_day. The
    aggregation does NOT pad with zero-call days — UI code can fill
    gaps if needed."""
    from engine.llm_telemetry import get_usage_summary, record_call

    record_call(source="commentary", model="claude-haiku-4-5-20251001",
                tokens_in=1, tokens_out=1)
    record_call(source="commentary", model="claude-haiku-4-5-20251001",
                tokens_in=1, tokens_out=1)
    _backdate_latest(days=3)  # 3 days ago, NOT yesterday or 2 days ago

    s = get_usage_summary(window_days=7)
    # Exactly two distinct dates: today + (today - 3 days). No padding.
    assert len(s["by_day"]) == 2


def test_summary_invalid_window_days_returns_empty() -> None:
    """Non-positive window_days returns the empty shape rather than
    raising or running an unbounded query."""
    from engine.llm_telemetry import get_usage_summary, record_call

    record_call(source="commentary", model="claude-haiku-4-5-20251001",
                tokens_in=1, tokens_out=1)

    s = get_usage_summary(window_days=0)
    assert s["total_calls"] == 0
    assert s["by_source"] == {}


# ═══════════════════════════════════════════════════════════════════════════
# get_recent_calls
# ═══════════════════════════════════════════════════════════════════════════

def test_recent_calls_empty_db_returns_empty_list() -> None:
    from engine.llm_telemetry import get_recent_calls

    assert get_recent_calls() == []


def test_recent_calls_ordered_newest_first() -> None:
    """Three rows inserted in known order — get_recent_calls returns
    them with the most recent first."""
    from engine.llm_telemetry import get_recent_calls, record_call

    # Insert with distinct tab_names so we can identify each row.
    record_call(source="commentary", model="claude-haiku-4-5-20251001",
                tokens_in=1, tokens_out=1, tab_name="first")
    _backdate_latest(days=2)
    record_call(source="commentary", model="claude-haiku-4-5-20251001",
                tokens_in=1, tokens_out=1, tab_name="second")
    _backdate_latest(days=1)
    record_call(source="commentary", model="claude-haiku-4-5-20251001",
                tokens_in=1, tokens_out=1, tab_name="third")
    # "third" stays at now, "second" is 1d old, "first" is 2d old.

    out = get_recent_calls()
    assert len(out) == 3
    # Newest first
    assert out[0]["tab_name"] == "third"
    assert out[1]["tab_name"] == "second"
    assert out[2]["tab_name"] == "first"


def test_recent_calls_respects_limit() -> None:
    """limit=N caps the returned rows even with many rows in the DB."""
    from engine.llm_telemetry import get_recent_calls, record_call

    for _ in range(5):
        record_call(source="commentary", model="claude-haiku-4-5-20251001",
                    tokens_in=1, tokens_out=1)

    assert len(get_recent_calls(limit=2)) == 2
    assert len(get_recent_calls(limit=10)) == 5  # caps at actual count


def test_recent_calls_returns_dict_per_row_with_full_shape() -> None:
    """Each returned row is a dict with every column populated."""
    from engine.llm_telemetry import get_recent_calls, record_call

    record_call(
        source="commentary",
        model="claude-haiku-4-5-20251001",
        tokens_in=42, tokens_out=17,
        cached_tokens=8, tab_name="overview",
    )

    out = get_recent_calls()
    assert len(out) == 1
    row = out[0]
    expected_keys = {
        "call_id", "created_at", "source", "tab_name", "model",
        "tokens_in", "tokens_out", "cached_tokens", "est_cost_usd",
    }
    assert set(row.keys()) == expected_keys
    assert row["source"] == "commentary"
    assert row["tab_name"] == "overview"
    assert row["tokens_in"] == 42
    assert row["tokens_out"] == 17
    assert row["cached_tokens"] == 8


# ═══════════════════════════════════════════════════════════════════════════
# Schema version sanity — confirms the v3 migration ran for the test DB
# ═══════════════════════════════════════════════════════════════════════════

def test_schema_v3_table_exists() -> None:
    """The llm_calls table must exist after get_connection() initializes
    a fresh DB — confirms the v3 migration is wired into _init_schema."""
    from state.db import get_connection

    conn = get_connection()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='llm_calls'"
    ).fetchall()
    assert len(rows) == 1


def test_schema_v3_indexes_exist() -> None:
    """Both indexes specified in the schema are created."""
    from state.db import get_connection

    conn = get_connection()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='llm_calls'"
    ).fetchall()
    names = {r["name"] for r in rows}
    assert "idx_llm_calls_created_at" in names
    assert "idx_llm_calls_source" in names


def test_schema_version_is_three() -> None:
    """Schema version constant matches the new value AND lands in
    kv_state.schema_version after init."""
    from state.db import SCHEMA_VERSION, get_connection

    assert SCHEMA_VERSION == 3
    conn = get_connection()
    row = conn.execute(
        "SELECT value FROM kv_state WHERE key = 'schema_version'"
    ).fetchone()
    assert int(row["value"]) == 3
