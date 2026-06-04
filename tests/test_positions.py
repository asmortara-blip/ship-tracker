"""Durable per-user position ledger (schema v29; rec R096)."""

from __future__ import annotations

import pytest

from state import positions as pos
from state.db import get_connection


@pytest.fixture(autouse=True)
def _clean_positions():
    # Each test starts from an empty positions table (the migration created it).
    conn = get_connection()
    conn.execute("DELETE FROM positions")
    conn.commit()
    yield
    conn.execute("DELETE FROM positions")
    conn.commit()


_BOOK = [
    {"ticker": "ZIM", "sector": "Container", "shares": 500, "avg_cost": 18.4, "beta": 1.85},
    {"ticker": "STNG", "sector": "Tanker", "shares": 400, "avg_cost": 52.3, "beta": 1.32},
]


def test_schema_v29_positions_table_exists() -> None:
    conn = get_connection()
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='positions'"
    ).fetchone()
    assert row is not None


def test_round_trip_open_positions() -> None:
    pos.replace_positions("alice", _BOOK)
    loaded = pos.load_positions("alice")
    assert {p["ticker"] for p in loaded} == {"ZIM", "STNG"}
    zim = next(p for p in loaded if p["ticker"] == "ZIM")
    assert zim["shares"] == 500 and zim["avg_cost"] == pytest.approx(18.4)
    assert zim["beta"] == pytest.approx(1.85)
    # only POSITION_FIELDS are surfaced (drop-in for the legacy list shape)
    assert set(zim.keys()) == set(pos.POSITION_FIELDS)


def test_positions_are_user_scoped() -> None:
    pos.replace_positions("alice", _BOOK)
    pos.replace_positions("bob", [{"ticker": "MATX", "shares": 10, "avg_cost": 100.0}])
    assert {p["ticker"] for p in pos.load_positions("alice")} == {"ZIM", "STNG"}
    assert {p["ticker"] for p in pos.load_positions("bob")} == {"MATX"}
    assert pos.load_positions("carol") == []  # an unknown user sees nothing


def test_replace_bumps_version_and_swaps_open_set() -> None:
    assert pos.current_version("alice") == 0
    pos.replace_positions("alice", _BOOK)
    assert pos.current_version("alice") == 1
    pos.replace_positions("alice", [{"ticker": "DAC", "shares": 300, "avg_cost": 74.2}])
    assert pos.current_version("alice") == 2
    # the open book is now only the v2 set
    assert {p["ticker"] for p in pos.load_positions("alice")} == {"DAC"}


def test_history_retains_closed_rows() -> None:
    pos.replace_positions("alice", _BOOK)              # v1: ZIM, STNG
    pos.replace_positions("alice", [{"ticker": "DAC", "shares": 1, "avg_cost": 1.0}])  # v2: DAC
    hist = pos.position_history("alice")
    # all three rows retained across two versions
    assert len(hist) == 3
    closed = [h for h in hist if h["closed_at"] is not None]
    open_rows = [h for h in hist if h["closed_at"] is None]
    assert {h["ticker"] for h in closed} == {"ZIM", "STNG"}  # v1 rows closed
    assert {h["ticker"] for h in open_rows} == {"DAC"}        # v2 row open
    assert {h["version"] for h in hist} == {1, 2}


def test_empty_replace_closes_everything() -> None:
    pos.replace_positions("alice", _BOOK)
    pos.replace_positions("alice", [])  # liquidate
    assert pos.load_positions("alice") == []
    assert len(pos.position_history("alice")) == 2  # both rows retained, closed


def test_missing_beta_tolerated() -> None:
    pos.replace_positions("alice", [{"ticker": "X", "shares": 5, "avg_cost": 2.0}])
    loaded = pos.load_positions("alice")
    assert loaded[0]["beta"] is None
