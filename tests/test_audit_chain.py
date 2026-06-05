"""Tamper-evident audit hash-chain (schema v31; rec R100)."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


def _seed(n: int) -> None:
    from auth.audit import record_audit
    for i in range(n):
        record_audit("act", entity_type="t", entity_id=str(i), detail={"i": i})


def test_v31_columns_exist() -> None:
    from state.db import get_connection, SCHEMA_VERSION
    cols = [r[1] for r in get_connection().execute("PRAGMA table_info(audit_events)")]
    assert SCHEMA_VERSION >= 31
    assert "prev_hash" in cols and "row_hash" in cols


def test_fresh_chain_verifies() -> None:
    from engine.audit_search import verify_chain
    _seed(5)
    res = verify_chain()
    assert res.ok
    assert res.n_chained == 5 and res.n_unchained == 0
    assert res.head and res.first_break_rowid is None


def test_each_row_links_to_predecessor() -> None:
    from state.db import get_connection
    _seed(4)
    rows = get_connection().execute(
        "SELECT prev_hash, row_hash FROM audit_events ORDER BY rowid"
    ).fetchall()
    assert rows[0]["prev_hash"] == ""               # genesis
    for prev, cur in zip(rows, rows[1:]):
        assert cur["prev_hash"] == prev["row_hash"]  # chained


def test_chain_head_tracks_last_row() -> None:
    from auth.audit import chain_head
    assert chain_head() is None                      # empty table
    _seed(3)
    head = chain_head()
    from engine.audit_search import verify_chain
    assert head["row_hash"] == verify_chain().head


def test_inplace_edit_is_detected() -> None:
    from engine.audit_search import verify_chain
    from state.db import get_connection
    _seed(5)
    # An attacker rewrites a row's payload but cannot recompute the stored hash.
    conn = get_connection()
    rid = conn.execute(
        "SELECT rowid FROM audit_events ORDER BY rowid LIMIT 1 OFFSET 2"
    ).fetchone()[0]
    with conn:
        conn.execute(
            "UPDATE audit_events SET detail_json = ? WHERE rowid = ?",
            ('{"i": 999}', rid),
        )
    res = verify_chain()
    assert not res.ok
    assert res.first_break_rowid == rid
    assert "modified" in res.first_break_reason


def test_row_deletion_is_detected() -> None:
    from engine.audit_search import verify_chain
    from state.db import get_connection
    _seed(5)
    conn = get_connection()
    rid = conn.execute(
        "SELECT rowid FROM audit_events ORDER BY rowid LIMIT 1 OFFSET 2"
    ).fetchone()[0]
    with conn:
        conn.execute("DELETE FROM audit_events WHERE rowid = ?", (rid,))
    res = verify_chain()
    assert not res.ok
    assert "link" in res.first_break_reason or "delete" in res.first_break_reason


def test_prefix_prune_is_tolerated() -> None:
    from engine.audit_search import verify_chain
    from state.db import get_connection
    _seed(6)
    conn = get_connection()
    # Delete the OLDEST two rows (a sanctioned retention prune of a prefix).
    rids = [r[0] for r in conn.execute(
        "SELECT rowid FROM audit_events ORDER BY rowid LIMIT 2"
    ).fetchall()]
    with conn:
        conn.executemany("DELETE FROM audit_events WHERE rowid = ?", [(r,) for r in rids])
    res = verify_chain()
    assert res.ok  # remaining suffix is still internally consistent
    assert res.n_chained == 4


def test_reseal_restores_validity_after_redaction() -> None:
    from engine.audit_search import reseal_chain, verify_chain
    from state.db import get_connection
    _seed(5)
    conn = get_connection()
    with conn:
        conn.execute("UPDATE audit_events SET detail_json = '{}'")  # redact all
    assert not verify_chain().ok            # redaction breaks the chain (evident)
    n = reseal_chain()
    assert n == 5
    assert verify_chain().ok                # re-sealed -> valid again


# ── anchored-head detection of tail tampering (R100 review hardening) ────────

def test_anchor_matches_untampered_chain() -> None:
    from auth.audit import chain_head
    from engine.audit_search import verify_chain
    _seed(3)
    res = verify_chain(expected_head=chain_head()["row_hash"])
    assert res.ok and res.head_matches_anchor is True


def test_tail_edit_undetectable_in_db_but_caught_by_anchor() -> None:
    from auth.audit import _compute_row_hash, chain_head
    from engine.audit_search import verify_chain
    from state.db import get_connection
    _seed(4)
    anchor = chain_head()["row_hash"]
    conn = get_connection()
    last = conn.execute(
        "SELECT rowid AS rid, prev_hash, event_id, created_at, user_id, "
        "action, entity_type, entity_id FROM audit_events ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    new_detail = '{"i": 999}'
    # Attacker edits the tail AND recomputes a self-consistent hash.
    new_hash = _compute_row_hash(
        last["prev_hash"], last["event_id"], last["created_at"], last["user_id"],
        last["action"], last["entity_type"], last["entity_id"], new_detail,
    )
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "UPDATE audit_events SET detail_json = ?, row_hash = ? WHERE rowid = ?",
        (new_detail, new_hash, last["rid"]),
    )
    conn.execute("COMMIT")
    assert verify_chain().ok  # in-DB walk alone cannot see a recomputed tail
    res = verify_chain(expected_head=anchor)
    assert not res.ok and res.head_matches_anchor is False


def test_suffix_truncation_caught_by_anchor() -> None:
    from auth.audit import chain_head
    from engine.audit_search import verify_chain
    from state.db import get_connection
    _seed(5)
    anchor = chain_head()["row_hash"]
    conn = get_connection()
    rid = conn.execute(
        "SELECT rowid FROM audit_events ORDER BY rowid DESC LIMIT 1"
    ).fetchone()[0]
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("DELETE FROM audit_events WHERE rowid = ?", (rid,))
    conn.execute("COMMIT")
    assert verify_chain().ok                  # internal chain still consistent
    res = verify_chain(expected_head=anchor)
    assert not res.ok and res.head_matches_anchor is False


def test_canonical_hash_resists_separator_injection() -> None:
    # Two rows that a naive separator-join could collide must hash differently.
    from auth.audit import _compute_row_hash
    a = _compute_row_hash("", "e", "t", "u", "act", "a", "b\x1fc", "{}")
    b = _compute_row_hash("", "e", "t", "u", "act", "a\x1fb", "c", "{}")
    assert a != b
