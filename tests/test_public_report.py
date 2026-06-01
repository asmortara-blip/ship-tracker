"""Tests for the read-only public-share-link feature in utils.report_history.

A public-share link is a URL-safe slug + expiry stored on a
``report_history`` row. Stakeholders without app access can read the
rendered HTML through ``load_public_report(slug)`` without ever seeing
the internal ``report_id`` or on-disk ``file_path``.

Covers:
  - make_public happy path generates a non-empty slug and a future expiry
  - make_public with an unknown report_id returns None
  - make_public with non-positive expires_in_days returns None
  - make_public computes an expires_at ~ N days in the future
  - revoke_public clears the slug and returns True
  - revoke_public for an unknown report_id returns False
  - load_public_report returns HTML for a valid slug
  - load_public_report returns None for unknown / empty / expired slug
  - load_public_report returns None after the on-disk HTML is deleted
  - load_public_report returns None after revoke_public
  - Generated slugs are URL-safe (base64url alphabet only)
"""
from __future__ import annotations

import re
import string
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from utils import report_history as rh
from utils.report_history import (
    ReportMeta,
    load_public_report,
    make_public,
    revoke_public,
    save_report,
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


# ─── Helpers ────────────────────────────────────────────────────────────────

class _FakeReport:
    market_sentiment = "BULLISH"
    sentiment_score = 0.5
    risk_level = "MODERATE"
    signal_count = 3
    data_quality = "FULL"
    date = "May 21, 2026"


def _save(html: str = "<html>body</html>") -> ReportMeta:
    meta = save_report(html, _FakeReport())
    assert meta is not None, "save_report should succeed in tests"
    return meta


def _row(report_id: str):
    from state.db import get_connection
    return get_connection().execute(
        "SELECT public_slug, public_expires_at FROM report_history "
        "WHERE report_id = ?",
        (report_id,),
    ).fetchone()


def _set_expires_at(report_id: str, expires_at: str) -> None:
    """Force-write a specific ``public_expires_at`` (used to simulate
    expiry without sleeping for hours)."""
    from state.db import get_connection
    with get_connection() as conn:
        conn.execute(
            "UPDATE report_history SET public_expires_at = ? "
            "WHERE report_id = ?",
            (expires_at, report_id),
        )


# ─── make_public ────────────────────────────────────────────────────────────

def test_make_public_happy_path_returns_non_empty_slug() -> None:
    meta = _save()
    slug = make_public(meta.report_id)
    assert isinstance(slug, str)
    assert len(slug) > 0

    row = _row(meta.report_id)
    assert row["public_slug"] == slug
    # expires_at must parse as a future timestamp.
    expires_at = datetime.fromisoformat(row["public_expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    assert expires_at > datetime.now(timezone.utc)


def test_make_public_unknown_report_id_returns_none() -> None:
    assert make_public("does-not-exist") is None


def test_make_public_non_positive_expires_returns_none() -> None:
    meta = _save()
    assert make_public(meta.report_id, expires_in_days=0) is None
    assert make_public(meta.report_id, expires_in_days=-1) is None
    # And the row stays un-shared.
    row = _row(meta.report_id)
    assert row["public_slug"] == ""
    assert row["public_expires_at"] == ""


def test_make_public_expiry_roughly_n_days_out() -> None:
    """With ``expires_in_days=1``, the stamped expires_at is within ±60s
    of (now + 1 day) — tolerant of clock jitter and slow CI runners."""
    meta = _save()
    before = datetime.now(timezone.utc)
    slug = make_public(meta.report_id, expires_in_days=1)
    after = datetime.now(timezone.utc)
    assert slug is not None

    expires_at = datetime.fromisoformat(_row(meta.report_id)["public_expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    expected_low = before + timedelta(days=1) - timedelta(seconds=60)
    expected_high = after + timedelta(days=1) + timedelta(seconds=60)
    assert expected_low <= expires_at <= expected_high


def test_make_public_slug_is_url_safe() -> None:
    """token_urlsafe is base64url — no /, +, =, or other unsafe chars."""
    meta = _save()
    slug = make_public(meta.report_id)
    assert slug is not None
    # base64url alphabet = A-Z a-z 0-9 - _
    allowed = set(string.ascii_letters + string.digits + "-_")
    assert all(c in allowed for c in slug), f"unexpected char in slug: {slug!r}"
    # Spot-check that the explicitly forbidden chars are absent.
    assert not re.search(r"[/+=]", slug)


# ─── revoke_public ──────────────────────────────────────────────────────────

def test_revoke_public_clears_slug_and_returns_true() -> None:
    meta = _save()
    slug = make_public(meta.report_id)
    assert slug is not None
    assert _row(meta.report_id)["public_slug"] == slug

    assert revoke_public(meta.report_id) is True
    row = _row(meta.report_id)
    assert row["public_slug"] == ""
    assert row["public_expires_at"] == ""


def test_revoke_public_unknown_report_id_returns_false() -> None:
    assert revoke_public("never-existed") is False


# ─── load_public_report ────────────────────────────────────────────────────

def test_load_public_report_returns_html_for_valid_slug() -> None:
    meta = _save("<html>secret</html>")
    slug = make_public(meta.report_id)
    assert slug is not None
    assert load_public_report(slug) == "<html>secret</html>"


def test_load_public_report_unknown_slug_returns_none() -> None:
    _save()
    assert load_public_report("not-a-real-slug") is None


def test_load_public_report_empty_slug_returns_none() -> None:
    """An empty string must never match the empty default column."""
    _save()  # row exists but is not shared (public_slug='')
    assert load_public_report("") is None


def test_load_public_report_after_expiry_returns_none() -> None:
    meta = _save()
    slug = make_public(meta.report_id)
    assert slug is not None
    # Force expiry into the past.
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    _set_expires_at(meta.report_id, past)
    assert load_public_report(slug) is None


def test_load_public_report_after_file_deleted_returns_none() -> None:
    meta = _save()
    slug = make_public(meta.report_id)
    assert slug is not None
    Path(meta.file_path).unlink()
    assert load_public_report(slug) is None


def test_load_public_report_after_revoke_returns_none() -> None:
    meta = _save()
    slug = make_public(meta.report_id)
    assert slug is not None
    assert revoke_public(meta.report_id) is True
    assert load_public_report(slug) is None


def test_load_public_report_bad_expires_at_returns_none() -> None:
    """A malformed expires_at must not crash the loader."""
    meta = _save()
    slug = make_public(meta.report_id)
    assert slug is not None
    _set_expires_at(meta.report_id, "not-a-timestamp")
    assert load_public_report(slug) is None
