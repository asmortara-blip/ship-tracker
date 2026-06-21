"""Tests for immutable report version + supersede/amend lineage (R119).

Covers:
  - the v34 migration: report_history grows ``report_version`` +
    ``supersedes_id``, and SCHEMA_VERSION is bumped + stamped;
  - the migration is additive + idempotent (re-running is a no-op);
  - amend_report mints a NEW row at version+1 with supersedes_id set, and
    NEVER mutates the original row;
  - a fresh report is version 1 with a chain of length 1;
  - version_chain returns the ordered (oldest→newest) lineage;
  - reissue_report re-stamps the same content as a new version;
  - the chain walk is cycle-guarded (terminates on a self/loop link).

Uses the same isolated-DB + isolated-REPORT_DIR fixture shape as
tests/test_report_history.py so no real cache/ or DB is touched.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from utils import report_history as rh
from utils.report_history import (
    amend_report,
    list_reports,
    load_report_html,
    reissue_report,
    save_report,
    version_chain,
)


# ─── Fixture: isolate REPORT_DIR + SQLite DB per test ──────────────────────

@pytest.fixture(autouse=True)
def isolated_storage(monkeypatch, tmp_path):
    """Redirect both the HTML file dir AND the SQLite DB so no test
    touches real cache/."""
    from state import db as state_db

    tmp_reports = tmp_path / "reports"
    monkeypatch.setattr(rh, "REPORT_DIR", tmp_reports)
    monkeypatch.setattr(rh, "_INDEX_FILE", tmp_reports / "report_index.json")
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


@dataclass
class _FakeReport:
    date: str = "January 15, 2026"
    market_sentiment: str = "BULLISH"
    sentiment_score: float = 0.65
    risk_level: str = "MODERATE"
    signal_count: int = 7
    data_quality: str = "FULL"


# ─── v34 migration ─────────────────────────────────────────────────────────

def test_v34_migration_adds_lineage_columns() -> None:
    """v34 ALTER TABLE adds report_version + supersedes_id to report_history."""
    from state.db import get_connection

    conn = get_connection()
    cols = {
        row["name"]: row
        for row in conn.execute("PRAGMA table_info(report_history)").fetchall()
    }
    assert "report_version" in cols
    assert "supersedes_id" in cols
    assert cols["report_version"]["type"].upper() == "INTEGER"
    assert cols["supersedes_id"]["type"].upper() == "TEXT"
    # supersedes_id is NULLable on purpose (head-of-chain marker).
    assert cols["supersedes_id"]["notnull"] == 0


def test_v34_report_version_defaults_to_one() -> None:
    """A bare insert (no version columns) lands at version 1, NULL supersedes."""
    from state.db import get_connection

    conn = get_connection()
    conn.execute(
        """
        INSERT INTO report_history
          (report_id, generated_at, file_path)
        VALUES ('rv34', '2026-06-06T00:00:00+00:00', '/tmp/x.html')
        """
    )
    row = conn.execute(
        "SELECT report_version, supersedes_id FROM report_history "
        "WHERE report_id = 'rv34'"
    ).fetchone()
    assert row["report_version"] == 1
    assert row["supersedes_id"] is None


def test_schema_version_bumped_to_34_and_stamped() -> None:
    from state.db import SCHEMA_VERSION, get_connection

    assert SCHEMA_VERSION >= 34
    conn = get_connection()
    row = conn.execute(
        "SELECT value FROM kv_state WHERE key = 'schema_version'"
    ).fetchone()
    assert row is not None
    assert int(row["value"]) == SCHEMA_VERSION


def test_v34_migration_is_idempotent() -> None:
    """Re-running _migrate_to_v34 on a fully-migrated DB is a no-op (the
    duplicate-column OperationalError is swallowed)."""
    from state.db import get_connection
    from state.migrations import _migrate_to_v34

    conn = get_connection()
    before = {
        r["name"] for r in conn.execute(
            "PRAGMA table_info(report_history)"
        ).fetchall()
    }
    # Run it several more times — must not raise and must not change shape.
    _migrate_to_v34(conn)
    _migrate_to_v34(conn)
    after = {
        r["name"] for r in conn.execute(
            "PRAGMA table_info(report_history)"
        ).fetchall()
    }
    assert before == after
    assert "report_version" in after and "supersedes_id" in after


# ─── Fresh report is v1, chain length 1 ────────────────────────────────────

def test_fresh_report_is_version_one() -> None:
    meta = save_report("<html>orig</html>", _FakeReport())
    assert meta is not None
    assert meta.report_version == 1
    assert meta.supersedes_id == ""


def test_fresh_report_chain_length_one() -> None:
    meta = save_report("<html>orig</html>", _FakeReport())
    chain = version_chain(meta.report_id)
    assert len(chain) == 1
    assert chain[0].report_id == meta.report_id
    assert chain[0].report_version == 1


def test_version_chain_unknown_id_returns_empty() -> None:
    assert version_chain("does-not-exist") == []


# ─── amend_report: new row, original untouched ─────────────────────────────

def test_amend_mints_new_row_version_plus_one() -> None:
    orig = save_report("<html>v1</html>", _FakeReport())
    new_id = amend_report(orig.report_id, "<html>v2 corrected</html>",
                          reason="typo fix")
    assert new_id is not None
    assert new_id != orig.report_id

    chain = version_chain(new_id)
    assert len(chain) == 2
    versions = [m.report_version for m in chain]
    assert versions == [1, 2]
    newest = chain[-1]
    assert newest.report_id == new_id
    assert newest.report_version == 2
    assert newest.supersedes_id == orig.report_id


def test_amend_does_not_mutate_original_row() -> None:
    orig = save_report("<html>v1 immutable</html>", _FakeReport())

    # Snapshot the ORIGINAL row before the amend.
    from state.db import get_connection
    conn = get_connection()
    before = dict(conn.execute(
        "SELECT * FROM report_history WHERE report_id = ?",
        (orig.report_id,),
    ).fetchone())

    new_id = amend_report(orig.report_id, "<html>v2</html>")
    assert new_id is not None

    after = dict(conn.execute(
        "SELECT * FROM report_history WHERE report_id = ?",
        (orig.report_id,),
    ).fetchone())
    assert after == before  # original row is byte-for-byte unchanged
    assert after["report_version"] == 1
    assert after["supersedes_id"] is None

    # Original HTML still loads + is unchanged.
    assert load_report_html(orig.report_id) == "<html>v1 immutable</html>"
    # The amendment loads its own (new) content.
    assert load_report_html(new_id) == "<html>v2</html>"


def test_amend_unknown_original_returns_none() -> None:
    assert amend_report("nope", "<html>x</html>") is None


def test_amend_then_amend_three_version_chain() -> None:
    orig = save_report("<html>v1</html>", _FakeReport())
    v2 = amend_report(orig.report_id, "<html>v2</html>")
    v3 = amend_report(v2, "<html>v3</html>")
    assert v3 is not None

    chain = version_chain(v3)
    assert [m.report_version for m in chain] == [1, 2, 3]
    assert [m.report_id for m in chain] == [orig.report_id, v2, v3]

    # The chain is reachable from ANY member, not just the latest.
    chain_from_orig = version_chain(orig.report_id)
    assert [m.report_id for m in chain_from_orig] == [orig.report_id, v2, v3]
    chain_from_v2 = version_chain(v2)
    assert [m.report_id for m in chain_from_v2] == [orig.report_id, v2, v3]


# ─── reissue_report ────────────────────────────────────────────────────────

def test_reissue_clones_content_as_new_version() -> None:
    orig = save_report("<html>same content</html>", _FakeReport())
    new_id = reissue_report(orig.report_id)
    assert new_id is not None and new_id != orig.report_id

    chain = version_chain(new_id)
    assert [m.report_version for m in chain] == [1, 2]
    # Same content re-issued under a new id.
    assert load_report_html(new_id) == "<html>same content</html>"
    # Original still intact.
    assert load_report_html(orig.report_id) == "<html>same content</html>"


def test_reissue_unknown_returns_none() -> None:
    assert reissue_report("nope") is None


# ─── Cycle guard ───────────────────────────────────────────────────────────

def test_version_chain_cycle_guard_terminates() -> None:
    """A corrupt self-referential supersedes_id must not spin forever."""
    from state.db import get_connection

    conn = get_connection()
    # Two rows pointing at each other (A supersedes B, B supersedes A).
    conn.execute(
        """
        INSERT INTO report_history
          (report_id, generated_at, file_path, report_version, supersedes_id)
        VALUES ('cycA', '2026-06-06T00:00:01+00:00', '/tmp/a.html', 2, 'cycB')
        """
    )
    conn.execute(
        """
        INSERT INTO report_history
          (report_id, generated_at, file_path, report_version, supersedes_id)
        VALUES ('cycB', '2026-06-06T00:00:00+00:00', '/tmp/b.html', 1, 'cycA')
        """
    )
    # Must return (not hang) and include each id at most once.
    chain = version_chain("cycA")
    ids = [m.report_id for m in chain]
    assert set(ids) == {"cycA", "cycB"}
    assert len(ids) == len(set(ids))  # no duplicates


def test_version_chain_self_loop_terminates() -> None:
    from state.db import get_connection

    conn = get_connection()
    conn.execute(
        """
        INSERT INTO report_history
          (report_id, generated_at, file_path, report_version, supersedes_id)
        VALUES ('selfloop', '2026-06-06T00:00:00+00:00', '/tmp/s.html', 1, 'selfloop')
        """
    )
    chain = version_chain("selfloop")
    assert [m.report_id for m in chain] == ["selfloop"]
