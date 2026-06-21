"""state.db.immediate_transaction — atomic multi-statement writes under
autocommit (the fix for the Ship-wide ``with conn:`` no-op)."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


def test_commits_on_success() -> None:
    from state.db import get_connection, immediate_transaction
    conn = get_connection()
    conn.execute("CREATE TEMP TABLE t (x INTEGER)")
    with immediate_transaction(conn):
        conn.execute("INSERT INTO t VALUES (1)")
        conn.execute("INSERT INTO t VALUES (2)")
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 2


def test_rolls_back_on_error_so_partial_write_does_not_persist() -> None:
    from state.db import get_connection, immediate_transaction
    conn = get_connection()
    conn.execute("CREATE TEMP TABLE t2 (x INTEGER)")
    with pytest.raises(ValueError):
        with immediate_transaction(conn):
            conn.execute("INSERT INTO t2 VALUES (1)")  # first write
            raise ValueError("boom")                    # then fail
    # The first write must NOT have leaked (this is the whole point — a bare
    # `with conn:` under autocommit would have left the '1' committed).
    assert conn.execute("SELECT COUNT(*) FROM t2").fetchone()[0] == 0
    assert not conn.in_transaction  # transaction cleanly closed


def test_nesting_joins_outer_transaction() -> None:
    from state.db import get_connection, immediate_transaction
    conn = get_connection()
    conn.execute("CREATE TEMP TABLE t3 (x INTEGER)")
    with immediate_transaction(conn):          # outer owns the txn
        conn.execute("INSERT INTO t3 VALUES (1)")
        with immediate_transaction(conn):      # inner joins (no nested BEGIN)
            conn.execute("INSERT INTO t3 VALUES (2)")
        # inner exit must NOT have committed/closed the outer txn
        assert conn.in_transaction
    assert conn.execute("SELECT COUNT(*) FROM t3").fetchone()[0] == 2


def test_nested_inner_error_rolls_back_whole_outer() -> None:
    from state.db import get_connection, immediate_transaction
    conn = get_connection()
    conn.execute("CREATE TEMP TABLE t4 (x INTEGER)")
    with pytest.raises(ValueError):
        with immediate_transaction(conn):
            conn.execute("INSERT INTO t4 VALUES (1)")
            with immediate_transaction(conn):
                conn.execute("INSERT INTO t4 VALUES (2)")
                raise ValueError("boom")
    assert conn.execute("SELECT COUNT(*) FROM t4").fetchone()[0] == 0
