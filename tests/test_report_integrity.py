"""Tests for content-hash tamper-evidence on saved reports (R121, v35).

Auditors need to prove that a distributed report's bytes match what was
issued. On ``save_report`` the rendered report bytes are SHA-256'd into the
new ``report_history.content_hash`` column; on read,
``verify_report_integrity`` recomputes the on-disk file's hash and compares.

Covers:
  - the v35 migration: report_history grows ``content_hash`` (TEXT, NULLable),
    SCHEMA_VERSION == 35, the migration is additive + idempotent (re-run no-op);
  - save_report stores a NON-empty content_hash that equals sha256 of the
    EXACT on-disk bytes;
  - verify_report_integrity returns "verified" for an untouched report;
  - mutating the on-disk file → "tampered" + an audit event is recorded;
  - a legacy row with NULL content_hash → "unhashed" (NOT "tampered");
  - a deleted file → "missing";
  - amend_report's new row is also hashed + verifies.

Uses the same isolated-DB + isolated-REPORT_DIR fixture shape as
tests/test_report_history.py / test_report_lineage.py so no real cache/ or
DB is touched.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest

from utils import report_history as rh
from utils.report_history import (
    amend_report,
    save_report,
    verify_report_integrity,
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


# ─── v35 migration ─────────────────────────────────────────────────────────

def test_v35_migration_adds_content_hash_column() -> None:
    """v35 ALTER TABLE adds content_hash (TEXT, NULLable) to report_history."""
    from state.db import get_connection

    conn = get_connection()
    cols = {
        row["name"]: row
        for row in conn.execute("PRAGMA table_info(report_history)").fetchall()
    }
    assert "content_hash" in cols
    assert cols["content_hash"]["type"].upper() == "TEXT"
    # NULLable on purpose — legacy rows stay NULL (= "unhashed").
    assert cols["content_hash"]["notnull"] == 0


def test_schema_version_is_35_and_stamped() -> None:
    from state.db import SCHEMA_VERSION, get_connection

    assert SCHEMA_VERSION == 35
    conn = get_connection()
    row = conn.execute(
        "SELECT value FROM kv_state WHERE key = 'schema_version'"
    ).fetchone()
    assert row is not None
    assert int(row["value"]) == SCHEMA_VERSION


def test_v35_migration_is_idempotent() -> None:
    """Re-running _migrate_to_v35 on a fully-migrated DB is a no-op (the
    duplicate-column OperationalError is swallowed)."""
    from state.db import get_connection
    from state.migrations import _migrate_to_v35

    conn = get_connection()
    before = {
        r["name"] for r in conn.execute(
            "PRAGMA table_info(report_history)"
        ).fetchall()
    }
    # Run it several more times — must not raise and must not change shape.
    _migrate_to_v35(conn)
    _migrate_to_v35(conn)
    after = {
        r["name"] for r in conn.execute(
            "PRAGMA table_info(report_history)"
        ).fetchall()
    }
    assert before == after
    assert "content_hash" in after


# ─── save_report stamps the hash over the exact on-disk bytes ──────────────

def test_save_report_stores_content_hash_matching_disk_bytes() -> None:
    meta = save_report("<html>tamper-evidence</html>", _FakeReport())
    assert meta is not None
    assert meta.content_hash  # non-empty

    # The stored hash must equal sha256 of the EXACT on-disk bytes.
    on_disk = Path(meta.file_path).read_bytes()
    assert meta.content_hash == hashlib.sha256(on_disk).hexdigest()

    # And it must be persisted on the row, not just on the returned meta.
    from state.db import get_connection
    row = get_connection().execute(
        "SELECT content_hash FROM report_history WHERE report_id = ?",
        (meta.report_id,),
    ).fetchone()
    assert row["content_hash"] == meta.content_hash


# ─── verify_report_integrity: verified / tampered / unhashed / missing ─────

def test_verify_untouched_report_is_verified() -> None:
    meta = save_report("<html>untouched</html>", _FakeReport())
    res = verify_report_integrity(meta.report_id)
    assert res["status"] == "verified"
    assert res["report_id"] == meta.report_id
    assert res["stored_hash"] == meta.content_hash
    assert res["actual_hash"] == meta.content_hash


def test_verify_mutated_file_is_tampered_and_audited() -> None:
    from auth.audit import query_audit

    meta = save_report("<html>original issued bytes</html>", _FakeReport())

    # Mutate the on-disk file underneath the issued hash.
    Path(meta.file_path).write_text("<html>TAMPERED</html>", encoding="utf-8")

    res = verify_report_integrity(meta.report_id)
    assert res["status"] == "tampered"
    assert res["stored_hash"] == meta.content_hash
    assert res["actual_hash"] != meta.content_hash
    # The recomputed hash matches the tampered bytes.
    assert res["actual_hash"] == hashlib.sha256(
        Path(meta.file_path).read_bytes()
    ).hexdigest()

    # A mismatch must leave an audit trail.
    events = query_audit(action="report_integrity_mismatch")
    assert any(e.entity_id == meta.report_id for e in events)


def test_verify_legacy_null_hash_is_unhashed_not_tampered() -> None:
    """A row with NULL content_hash (legacy pre-v35) is honestly 'unhashed',
    never 'tampered'."""
    meta = save_report("<html>legacy</html>", _FakeReport())

    # Null out the stored hash to simulate a pre-v35 row.
    from state.db import get_connection
    conn = get_connection()
    with conn:
        conn.execute(
            "UPDATE report_history SET content_hash = NULL WHERE report_id = ?",
            (meta.report_id,),
        )

    res = verify_report_integrity(meta.report_id)
    assert res["status"] == "unhashed"
    assert res["stored_hash"] == ""


def test_verify_deleted_file_is_missing() -> None:
    meta = save_report("<html>to be deleted</html>", _FakeReport())
    Path(meta.file_path).unlink()

    res = verify_report_integrity(meta.report_id)
    assert res["status"] == "missing"


def test_verify_unknown_report_is_missing() -> None:
    res = verify_report_integrity("does-not-exist")
    assert res["status"] == "missing"


# ─── amend_report's new version is also hashed + verifies ──────────────────

def test_amend_report_new_row_is_hashed_and_verifies() -> None:
    orig = save_report("<html>v1</html>", _FakeReport())
    new_id = amend_report(orig.report_id, "<html>v2 corrected</html>",
                          reason="typo fix")
    assert new_id is not None
    assert new_id != orig.report_id

    # The new version's row must carry a non-empty hash over its own bytes.
    from state.db import get_connection
    row = get_connection().execute(
        "SELECT file_path, content_hash FROM report_history WHERE report_id = ?",
        (new_id,),
    ).fetchone()
    assert row["content_hash"]
    on_disk = Path(row["file_path"]).read_bytes()
    assert row["content_hash"] == hashlib.sha256(on_disk).hexdigest()

    # And it verifies clean.
    res = verify_report_integrity(new_id)
    assert res["status"] == "verified"

    # The ORIGINAL still verifies too (untouched by the amend).
    assert verify_report_integrity(orig.report_id)["status"] == "verified"
