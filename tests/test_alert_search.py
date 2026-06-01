"""Tests for ``engine.alert_search`` — operator-facing alert-table search.

Covers the dataclass shape, the public helpers (``search_alerts`` /
``search_alerts_count`` / ``get_distinct_*``), and the defining
properties:

* ``search_alerts`` NEVER raises (DB outage → empty result).
* Every filter is independently optional and composes via AND.
* ``severity_min`` is tier-aware (``HIGH`` keeps HIGH and CRITICAL).
* ``text`` is case-insensitive and grep's ``title || ' ' || body``.
* ``LIMIT`` clips the row set without disturbing ``total_matched``.
* User-supplied ``%`` / ``_`` / ``'`` in ``text`` are LIKE-escaped, not
  interpreted — a search for a literal underscore matches the literal
  character, not "any single character".
* Per-user scope uses the dual-set semantics shared with the rest of
  the codebase (own rows + legacy ``user_id=''`` rows).
"""
from __future__ import annotations

import uuid

import pytest


# ─── Fixture: isolate persistence to a tmp file per test ────────────────────

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Redirect the SQLite state DB to a per-test tmp_path so no test
    touches the real cache/ship_tracker.db."""
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Helper: seed a deterministic set of alert rows ────────────────────────

def _seed(rows: list[dict]) -> None:
    """Insert the given rows directly so the test can pin ``created_at``
    timestamps and exact column values. The real ``save_alerts`` stamps
    its own UUID + does dedup work that would make ORDER BY assertions
    flaky.

    Every field has a defaulted value so tests can override exactly the
    columns they care about and ignore the rest.
    """
    from state.db import get_connection

    conn = get_connection()
    with conn:
        for r in rows:
            conn.execute(
                """
                INSERT INTO alerts
                  (alert_id, created_at, alert_type, severity, title,
                   body, ticker, route_id, port_locode, value, threshold,
                   change_pct, acknowledged, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    r.get("alert_id", str(uuid.uuid4())),
                    r["created_at"],
                    r.get("alert_type", "BDI_MOVE"),
                    r.get("severity", "HIGH"),
                    r.get("title", "Alert title"),
                    r.get("body", "Alert body"),
                    r.get("ticker", ""),
                    r.get("route_id", ""),
                    r.get("port_locode", ""),
                    r.get("value", 0.0),
                    r.get("threshold", 0.0),
                    r.get("change_pct", 0.0),
                    1 if r.get("acknowledged", False) else 0,
                    r.get("user_id", ""),
                ),
            )


# ─── search_alerts: empty-query / ordering ─────────────────────────────────

def test_search_alerts_empty_query_returns_recent_descending() -> None:
    """No filters → every row, newest-first by ``created_at``."""
    from engine.alert_search import AlertSearchQuery, search_alerts

    _seed([
        {"alert_id": "a1", "created_at": "2026-05-22T10:00:00+00:00"},
        {"alert_id": "a2", "created_at": "2026-05-22T12:00:00+00:00"},
        {"alert_id": "a3", "created_at": "2026-05-22T11:00:00+00:00"},
    ])

    result = search_alerts(AlertSearchQuery())
    assert result.total_matched == 3
    assert len(result.alerts) == 3
    assert [a["alert_id"] for a in result.alerts] == ["a2", "a3", "a1"]


# ─── search_alerts: severity (exact + tier-min) ────────────────────────────

def test_search_alerts_severity_exact_match() -> None:
    from engine.alert_search import AlertSearchQuery, search_alerts

    _seed([
        {"alert_id": "a1", "created_at": "2026-05-22T10:00:00+00:00",
         "severity": "CRITICAL"},
        {"alert_id": "a2", "created_at": "2026-05-22T10:01:00+00:00",
         "severity": "HIGH"},
        {"alert_id": "a3", "created_at": "2026-05-22T10:02:00+00:00",
         "severity": "MEDIUM"},
    ])

    result = search_alerts(AlertSearchQuery(severity="HIGH"))
    assert result.total_matched == 1
    assert result.alerts[0]["severity"] == "HIGH"


def test_search_alerts_severity_min_high_includes_critical() -> None:
    """``severity_min='HIGH'`` returns HIGH and CRITICAL, drops MEDIUM/LOW."""
    from engine.alert_search import AlertSearchQuery, search_alerts

    _seed([
        {"alert_id": "crit", "created_at": "2026-05-22T10:00:00+00:00",
         "severity": "CRITICAL"},
        {"alert_id": "high", "created_at": "2026-05-22T10:01:00+00:00",
         "severity": "HIGH"},
        {"alert_id": "med", "created_at": "2026-05-22T10:02:00+00:00",
         "severity": "MEDIUM"},
        {"alert_id": "low", "created_at": "2026-05-22T10:03:00+00:00",
         "severity": "LOW"},
    ])

    result = search_alerts(AlertSearchQuery(severity_min="HIGH"))
    assert result.total_matched == 2
    severities = sorted(a["severity"] for a in result.alerts)
    assert severities == ["CRITICAL", "HIGH"]


def test_search_alerts_severity_min_unknown_tier_is_dropped() -> None:
    """An unknown severity_min tier drops the predicate (returns all rows)
    rather than returning the empty set — the alternative ("show
    nothing") is a silent footgun."""
    from engine.alert_search import AlertSearchQuery, search_alerts

    _seed([
        {"alert_id": "a1", "created_at": "2026-05-22T10:00:00+00:00",
         "severity": "HIGH"},
        {"alert_id": "a2", "created_at": "2026-05-22T10:01:00+00:00",
         "severity": "LOW"},
    ])

    result = search_alerts(AlertSearchQuery(severity_min="EXTREME"))
    assert result.total_matched == 2


# ─── search_alerts: per-column filters ─────────────────────────────────────

def test_search_alerts_filters_by_alert_type() -> None:
    from engine.alert_search import AlertSearchQuery, search_alerts

    _seed([
        {"alert_id": "a1", "created_at": "2026-05-22T10:00:00+00:00",
         "alert_type": "BDI_MOVE"},
        {"alert_id": "a2", "created_at": "2026-05-22T10:01:00+00:00",
         "alert_type": "CONGESTION"},
        {"alert_id": "a3", "created_at": "2026-05-22T10:02:00+00:00",
         "alert_type": "RATE_SURGE"},
    ])

    result = search_alerts(AlertSearchQuery(alert_type="CONGESTION"))
    assert result.total_matched == 1
    assert result.alerts[0]["alert_type"] == "CONGESTION"


def test_search_alerts_filters_by_ticker() -> None:
    from engine.alert_search import AlertSearchQuery, search_alerts

    _seed([
        {"alert_id": "a1", "created_at": "2026-05-22T10:00:00+00:00",
         "ticker": "ZIM"},
        {"alert_id": "a2", "created_at": "2026-05-22T10:01:00+00:00",
         "ticker": "MAERSK"},
    ])

    result = search_alerts(AlertSearchQuery(ticker="ZIM"))
    assert result.total_matched == 1
    assert result.alerts[0]["ticker"] == "ZIM"


def test_search_alerts_filters_by_port_locode_and_route_id() -> None:
    from engine.alert_search import AlertSearchQuery, search_alerts

    _seed([
        {"alert_id": "a1", "created_at": "2026-05-22T10:00:00+00:00",
         "port_locode": "USLAX", "route_id": "transpac"},
        {"alert_id": "a2", "created_at": "2026-05-22T10:01:00+00:00",
         "port_locode": "NLRTM", "route_id": "transatl"},
        {"alert_id": "a3", "created_at": "2026-05-22T10:02:00+00:00",
         "port_locode": "USLAX", "route_id": "transatl"},
    ])

    r_port = search_alerts(AlertSearchQuery(port_locode="USLAX"))
    assert r_port.total_matched == 2

    r_route = search_alerts(AlertSearchQuery(route_id="transpac"))
    assert r_route.total_matched == 1
    assert r_route.alerts[0]["alert_id"] == "a1"

    r_both = search_alerts(AlertSearchQuery(
        port_locode="USLAX", route_id="transatl",
    ))
    assert r_both.total_matched == 1
    assert r_both.alerts[0]["alert_id"] == "a3"


def test_search_alerts_filters_by_acknowledged_true_and_false() -> None:
    from engine.alert_search import AlertSearchQuery, search_alerts

    _seed([
        {"alert_id": "ack", "created_at": "2026-05-22T10:00:00+00:00",
         "acknowledged": True},
        {"alert_id": "unack-a", "created_at": "2026-05-22T10:01:00+00:00",
         "acknowledged": False},
        {"alert_id": "unack-b", "created_at": "2026-05-22T10:02:00+00:00",
         "acknowledged": False},
    ])

    r_ack = search_alerts(AlertSearchQuery(acknowledged=True))
    assert r_ack.total_matched == 1
    assert r_ack.alerts[0]["alert_id"] == "ack"

    r_unack = search_alerts(AlertSearchQuery(acknowledged=False))
    assert r_unack.total_matched == 2
    ids = sorted(a["alert_id"] for a in r_unack.alerts)
    assert ids == ["unack-a", "unack-b"]

    # ``None`` (default) keeps both.
    r_all = search_alerts(AlertSearchQuery(acknowledged=None))
    assert r_all.total_matched == 3


def test_search_alerts_filters_by_since_until_window() -> None:
    """Half-open: ``since`` is inclusive, ``until`` is exclusive."""
    from engine.alert_search import AlertSearchQuery, search_alerts

    _seed([
        {"alert_id": "before", "created_at": "2026-05-22T09:00:00+00:00"},
        {"alert_id": "inside-a", "created_at": "2026-05-22T10:00:00+00:00"},
        {"alert_id": "inside-b", "created_at": "2026-05-22T11:00:00+00:00"},
        {"alert_id": "after", "created_at": "2026-05-22T12:00:00+00:00"},
    ])

    result = search_alerts(AlertSearchQuery(
        since="2026-05-22T10:00:00+00:00",
        until="2026-05-22T12:00:00+00:00",
    ))
    assert result.total_matched == 2
    ids = sorted(a["alert_id"] for a in result.alerts)
    assert ids == ["inside-a", "inside-b"]


# ─── search_alerts: text grep ──────────────────────────────────────────────

def test_search_alerts_text_grep_matches_title() -> None:
    from engine.alert_search import AlertSearchQuery, search_alerts

    _seed([
        {"alert_id": "suez", "created_at": "2026-05-22T10:00:00+00:00",
         "title": "Suez Canal congestion spike",
         "body": "Vessels stacked at SCA northbound."},
        {"alert_id": "panama", "created_at": "2026-05-22T10:01:00+00:00",
         "title": "Panama draft restrictions",
         "body": "Gatun lake low."},
    ])

    result = search_alerts(AlertSearchQuery(text="suez"))
    assert result.total_matched == 1
    assert result.alerts[0]["alert_id"] == "suez"


def test_search_alerts_text_grep_matches_body() -> None:
    """The grep covers the body too — operators search for free-text
    substrings ("DB connections") that often live only in the body."""
    from engine.alert_search import AlertSearchQuery, search_alerts

    _seed([
        {"alert_id": "db", "created_at": "2026-05-22T10:00:00+00:00",
         "title": "Worker health",
         "body": "DB connections exhausted on shard 3."},
        {"alert_id": "other", "created_at": "2026-05-22T10:01:00+00:00",
         "title": "Worker health",
         "body": "Queue backlog growing."},
    ])

    result = search_alerts(AlertSearchQuery(text="db connections"))
    assert result.total_matched == 1
    assert result.alerts[0]["alert_id"] == "db"


def test_search_alerts_text_grep_is_case_insensitive() -> None:
    from engine.alert_search import AlertSearchQuery, search_alerts

    _seed([
        {"alert_id": "a1", "created_at": "2026-05-22T10:00:00+00:00",
         "title": "Suez Canal disruption", "body": "Northbound stack."},
    ])

    for term in ("suez", "SUEZ", "Suez", "sUeZ"):
        result = search_alerts(AlertSearchQuery(text=term))
        assert result.total_matched == 1, f"failed for casing: {term!r}"


# ─── search_alerts: AND composition ────────────────────────────────────────

def test_search_alerts_combined_filters_and_together() -> None:
    from engine.alert_search import AlertSearchQuery, search_alerts

    _seed([
        # Should match: high+ severity, alert_type=CONGESTION, user=alice,
        # inside window, title grep "Suez".
        {"alert_id": "match", "created_at": "2026-05-22T10:30:00+00:00",
         "severity": "HIGH", "alert_type": "CONGESTION",
         "user_id": "alice", "title": "Suez Canal stack"},
        # Wrong severity tier.
        {"alert_id": "wrong-sev", "created_at": "2026-05-22T10:30:00+00:00",
         "severity": "MEDIUM", "alert_type": "CONGESTION",
         "user_id": "alice", "title": "Suez Canal stack"},
        # Wrong user.
        {"alert_id": "wrong-user", "created_at": "2026-05-22T10:30:00+00:00",
         "severity": "HIGH", "alert_type": "CONGESTION",
         "user_id": "bob", "title": "Suez Canal stack"},
        # Out of window.
        {"alert_id": "out-window", "created_at": "2026-05-22T08:30:00+00:00",
         "severity": "HIGH", "alert_type": "CONGESTION",
         "user_id": "alice", "title": "Suez Canal stack"},
    ])

    result = search_alerts(AlertSearchQuery(
        user_id="alice",
        severity_min="HIGH",
        alert_type="CONGESTION",
        since="2026-05-22T10:00:00+00:00",
        text="suez",
    ))
    assert result.total_matched == 1
    assert result.alerts[0]["alert_id"] == "match"


# ─── search_alerts_count ───────────────────────────────────────────────────

def test_search_alerts_count_returns_count_without_fetching() -> None:
    from engine.alert_search import AlertSearchQuery, search_alerts_count

    _seed([
        {"alert_id": f"a{i}", "created_at": f"2026-05-22T10:0{i}:00+00:00",
         "alert_type": "BDI_MOVE", "user_id": "alice"}
        for i in range(5)
    ])

    n = search_alerts_count(AlertSearchQuery(
        user_id="alice", alert_type="BDI_MOVE",
    ))
    assert n == 5


# ─── search_alerts: LIMIT semantics ────────────────────────────────────────

def test_search_alerts_limit_caps_results_total_matched_reflects_all() -> None:
    """``LIMIT`` clips alerts but ``total_matched`` keeps the full count
    so a paginating UI can render 'showing N of M'."""
    from engine.alert_search import AlertSearchQuery, search_alerts

    _seed([
        {"alert_id": f"a{i:02d}",
         "created_at": f"2026-05-22T10:00:{i:02d}+00:00",
         "alert_type": "BDI_MOVE", "user_id": "alice"}
        for i in range(20)
    ])

    result = search_alerts(AlertSearchQuery(
        user_id="alice", alert_type="BDI_MOVE", limit=5,
    ))
    assert result.total_matched == 20
    assert len(result.alerts) == 5


# ─── Distinct-value dropdown helpers ───────────────────────────────────────

def test_get_distinct_alert_types_returns_sorted_unique_list() -> None:
    from engine.alert_search import get_distinct_alert_types

    _seed([
        {"alert_id": "a1", "created_at": "2026-05-22T10:00:00+00:00",
         "alert_type": "RATE_SURGE"},
        {"alert_id": "a2", "created_at": "2026-05-22T10:01:00+00:00",
         "alert_type": "CONGESTION"},
        {"alert_id": "a3", "created_at": "2026-05-22T10:02:00+00:00",
         "alert_type": "CONGESTION"},  # duplicate — DISTINCT must dedupe
        {"alert_id": "a4", "created_at": "2026-05-22T10:03:00+00:00",
         "alert_type": "BDI_MOVE"},
    ])

    types = get_distinct_alert_types()
    assert types == sorted(types)
    assert types == ["BDI_MOVE", "CONGESTION", "RATE_SURGE"]


def test_get_distinct_tickers_returns_sorted_unique_list() -> None:
    from engine.alert_search import get_distinct_tickers

    _seed([
        {"alert_id": "a1", "created_at": "2026-05-22T10:00:00+00:00",
         "ticker": "ZIM"},
        {"alert_id": "a2", "created_at": "2026-05-22T10:01:00+00:00",
         "ticker": "MAERSK"},
        {"alert_id": "a3", "created_at": "2026-05-22T10:02:00+00:00",
         "ticker": "ZIM"},  # duplicate
        # Empty ticker should be filtered out — the schema default for
        # non-equity alerts is ``""`` and we don't want that to clutter
        # the dropdown.
        {"alert_id": "a4", "created_at": "2026-05-22T10:03:00+00:00",
         "ticker": ""},
    ])

    tickers = get_distinct_tickers()
    assert tickers == sorted(tickers)
    assert tickers == ["MAERSK", "ZIM"]


# ─── Defensive contract: NEVER raises ──────────────────────────────────────

def test_search_alerts_never_raises_on_db_error(monkeypatch) -> None:
    """A failing DB layer must surface an empty result, not an exception."""
    from engine import alert_search

    class _BoomConn:
        def execute(self, *a, **kw):  # noqa: ARG002
            raise RuntimeError("DB melted")

    # Patch the lazy get_connection import inside alert_search.
    monkeypatch.setattr(
        "state.db.get_connection", lambda: _BoomConn()
    )

    result = alert_search.search_alerts(alert_search.AlertSearchQuery())
    assert result.alerts == []
    assert result.total_matched == 0
    # The query is echoed back so the UI can render "no matches" with
    # the original filter set intact.
    assert result.query is not None


# ─── SQL injection / LIKE-escape safety ────────────────────────────────────

def test_search_alerts_text_with_sql_meta_chars_does_not_break() -> None:
    """``%`` / ``_`` / ``'`` in user-supplied text are bound, not
    interpolated, and LIKE meta-chars are escaped so they match
    literally."""
    from engine.alert_search import AlertSearchQuery, search_alerts

    _seed([
        # Row whose body contains a literal underscore + percent.
        {"alert_id": "literal", "created_at": "2026-05-22T10:00:00+00:00",
         "title": "key_with_%_in_it", "body": "diagnostic"},
        # Row that should NOT match a literal underscore search.
        {"alert_id": "no-underscore", "created_at": "2026-05-22T10:01:00+00:00",
         "title": "key-with-dash", "body": "diagnostic"},
        # Row whose title contains a single quote — a naive interpolated
        # query would explode here.
        {"alert_id": "quoted", "created_at": "2026-05-22T10:02:00+00:00",
         "title": "alice's note", "body": "diagnostic"},
    ])

    # Literal underscore — the LIKE wildcard ``_`` would match any one
    # char, but we escape it so only the literal underscore matches.
    r1 = search_alerts(AlertSearchQuery(text="key_with"))
    assert r1.total_matched == 1
    assert r1.alerts[0]["alert_id"] == "literal"

    # Literal percent.
    r2 = search_alerts(AlertSearchQuery(text="%"))
    assert r2.total_matched == 1
    assert r2.alerts[0]["alert_id"] == "literal"

    # Single quote — bound parameter must not break the query.
    r3 = search_alerts(AlertSearchQuery(text="alice's"))
    assert r3.total_matched == 1
    assert r3.alerts[0]["alert_id"] == "quoted"


# ─── Per-user scoping (dual-set semantics) ─────────────────────────────────

def test_search_alerts_user_id_scope_includes_legacy_rows() -> None:
    """When ``user_id`` is supplied, the search returns rows owned by
    that user PLUS legacy ``user_id=''`` rows — matching the dual-set
    semantics ``load_alerts`` uses so the search panel never hides a
    row that the operator can see in the main table."""
    from engine.alert_search import AlertSearchQuery, search_alerts

    _seed([
        {"alert_id": "alice-row", "created_at": "2026-05-22T10:00:00+00:00",
         "user_id": "alice"},
        {"alert_id": "legacy-row", "created_at": "2026-05-22T10:01:00+00:00",
         "user_id": ""},
        {"alert_id": "bob-row", "created_at": "2026-05-22T10:02:00+00:00",
         "user_id": "bob"},
    ])

    result = search_alerts(AlertSearchQuery(user_id="alice"))
    ids = sorted(a["alert_id"] for a in result.alerts)
    assert ids == ["alice-row", "legacy-row"]
    # Critically, bob's row is NOT returned.
    assert "bob-row" not in ids


# ─── JSONL helper ──────────────────────────────────────────────────────────

def test_alerts_to_jsonl_round_trips_each_row() -> None:
    """The wrapper produces one JSON line per row, terminated by ``\\n``,
    with no envelope wrapper (SIEM scrapers expect line-delimited JSON)."""
    import json

    from engine.alert_search import alerts_to_jsonl

    rows = [
        {"alert_id": "a1", "title": "t1", "severity": "HIGH"},
        {"alert_id": "a2", "title": "t2", "severity": "LOW"},
    ]
    payload = alerts_to_jsonl(rows)
    text = payload.decode("utf-8")

    # Trailing newline + exactly two lines when split on \n.
    assert text.endswith("\n")
    lines = [ln for ln in text.split("\n") if ln]
    assert len(lines) == 2

    parsed = [json.loads(ln) for ln in lines]
    assert parsed[0]["alert_id"] == "a1"
    assert parsed[1]["severity"] == "LOW"
