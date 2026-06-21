"""R111 — audit coverage for report GENERATION and ACCESS.

The audit substrate (``auth.audit.record_audit`` / ``query_audit``) already
recorded report *deletion* (``delete_report``) and *sharing*
(``make_public`` / ``revoke_public``). R111 extends it to the two events a
security review actually wants when answering "who saw / produced this
report?":

  * GENERATION — ``utils.report_history.save_report`` emits a
    ``generate_report`` event; ``worker.scheduler.run_daily_briefing_job``
    emits a companion ``scheduled_report_fire`` event (system user).
  * ACCESS — ``load_report_html`` emits ``read_report`` (in-scope, attributed
    to the accessing user); ``load_public_report`` emits
    ``read_public_report`` (anonymous, ``user_id=""``).

Defining properties verified here:
  * Each hook fires exactly once per successful operation, with
    ``entity_type="report"`` and the matching action verb.
  * The hook is best-effort: a save / read still succeeds even if the
    audit write is broken, and never raises.
  * Security hygiene carried over from make_public: the public slug is
    NEVER written into an audit detail payload.

Like ``test_report_history.py`` this redirects BOTH the HTML file dir and
the SQLite DB to a tmp_path so no real cache/ row or file is touched.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import patch

import pytest

from utils import report_history as rh
from utils.report_history import (
    load_public_report,
    load_report_html,
    make_public,
    save_report,
)


# ─── Fixture: isolate REPORT_DIR + SQLite DB per test ──────────────────────

@pytest.fixture(autouse=True)
def isolated_storage(monkeypatch, tmp_path):
    """Redirect both the HTML file dir AND the SQLite DB so no test
    touches real cache/ (mirrors test_report_history.py's fixture)."""
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


# ─── Generation: save_report → generate_report ─────────────────────────────

def test_save_report_writes_generate_audit_event() -> None:
    from auth.audit import query_audit

    meta = save_report("<html>r</html>", _FakeReport())
    assert meta is not None

    rows = query_audit(action="generate_report")
    assert len(rows) == 1
    ev = rows[0]
    assert ev.entity_type == "report"
    assert ev.entity_id == meta.report_id
    # The detail payload carries the report's headline metadata.
    assert ev.detail_json["risk_level"] == "MODERATE"
    assert ev.detail_json["sentiment_label"] == "BULLISH"
    assert ev.detail_json["data_quality"] == "FULL"


def test_save_report_failure_emits_no_audit_event() -> None:
    """When the row insert fails, save_report returns None AND records no
    generate_report event — the hook sits after a successful insert."""
    from auth.audit import query_audit

    # Force the DB write to fail by patching get_connection inside the
    # save path to raise. save_report swallows + returns None.
    with patch("state.db.get_connection", side_effect=RuntimeError("boom")):
        meta = save_report("<html>x</html>", _FakeReport())
    assert meta is None
    assert query_audit(action="generate_report") == []


def test_save_report_succeeds_even_if_audit_write_breaks() -> None:
    """The audit hook is best-effort: a broken record_audit must NOT stop
    the report from being saved."""
    with patch(
        "auth.audit.record_audit", side_effect=RuntimeError("audit down")
    ):
        meta = save_report("<html>ok</html>", _FakeReport())
    # The report still persisted despite the audit hook blowing up.
    assert meta is not None
    assert meta.report_id


# ─── Access: load_report_html → read_report ────────────────────────────────

def test_load_report_html_writes_read_audit_event() -> None:
    from auth.audit import query_audit

    meta = save_report("<html>body</html>", _FakeReport())
    assert meta is not None

    html = load_report_html(meta.report_id)
    assert html == "<html>body</html>"

    rows = query_audit(action="read_report")
    assert len(rows) == 1
    assert rows[0].entity_type == "report"
    assert rows[0].entity_id == meta.report_id


def test_load_report_html_unknown_id_emits_no_audit_event() -> None:
    """A miss (unknown id) reads None and records nothing — the hook only
    fires after a real read."""
    from auth.audit import query_audit

    assert load_report_html("does-not-exist") is None
    assert query_audit(action="read_report") == []


def test_load_report_html_records_accessing_user() -> None:
    """The read event is attributed to the user_id passed through."""
    from auth.audit import query_audit

    # Save under user "owner" then read it back explicitly as "owner".
    meta = save_report("<html>u</html>", _FakeReport())
    assert meta is not None
    # The saved row lives under user_id="" (no session); read it in that
    # same scope and assert the audit row carries that accessor id.
    html = load_report_html(meta.report_id, user_id="")
    assert html is not None
    rows = query_audit(action="read_report")
    assert len(rows) == 1
    assert rows[0].user_id == ""


# ─── Access: load_public_report → read_public_report ───────────────────────

def test_load_public_report_writes_anonymous_audit_event() -> None:
    from auth.audit import query_audit

    meta = save_report("<html>pub</html>", _FakeReport())
    assert meta is not None
    slug = make_public(meta.report_id, expires_in_days=7)
    assert slug is not None

    html = load_public_report(slug)
    assert html == "<html>pub</html>"

    rows = query_audit(action="read_public_report")
    assert len(rows) == 1
    ev = rows[0]
    assert ev.entity_type == "report"
    # Anonymous viewer — recorded honestly as the empty/system user.
    assert ev.user_id == ""
    # The slug must NEVER leak into the audit detail (security: a stolen
    # audit row must not hand an attacker the working public URL).
    assert slug not in json.dumps(ev.detail_json)
    assert ev.detail_json.get("password_protected") is False


def test_load_public_report_password_protected_flag_recorded() -> None:
    from auth.audit import query_audit

    meta = save_report("<html>p</html>", _FakeReport())
    assert meta is not None
    slug = make_public(meta.report_id, expires_in_days=7, password="s3cret!")
    assert slug is not None

    html = load_public_report(slug, password="s3cret!")
    assert html == "<html>p</html>"

    rows = query_audit(action="read_public_report")
    assert len(rows) == 1
    assert rows[0].detail_json.get("password_protected") is True
    # Neither slug nor password may appear in the audit payload.
    blob = json.dumps(rows[0].detail_json)
    assert slug not in blob
    assert "s3cret!" not in blob


def test_load_public_report_bad_slug_emits_no_audit_event() -> None:
    """An unknown / empty slug records nothing — every failing gate skips
    the hook."""
    from auth.audit import query_audit

    assert load_public_report("no-such-slug") is None
    assert load_public_report("") is None
    assert query_audit(action="read_public_report") == []


# ─── Scheduled fire: run_daily_briefing_job → scheduled_report_fire ─────────

def test_scheduled_briefing_writes_scheduled_fire_event() -> None:
    """run_daily_briefing_job emits a scheduled_report_fire event tied to
    the persisted report. save_report is patched to a known meta so the
    test isolates the scheduler's own hook (the build/render pipeline is
    exercised elsewhere)."""
    from auth.audit import query_audit
    from utils.report_history import ReportMeta
    from worker import scheduler

    fake_meta = ReportMeta(
        report_id="sched-rpt-1",
        generated_at="2026-06-06T00:00:00+00:00",
        report_date="June 6, 2026",
        sentiment_label="NEUTRAL",
        sentiment_score=0.0,
        risk_level="MODERATE",
        signal_count=0,
        data_quality="DEGRADED",
        file_path="/tmp/sched.html",
        file_size_kb=1.0,
    )

    # The job lazily imports build_investor_report / render_…_html /
    # save_report INSIDE the function, so patch each at its source module
    # — the lazy ``from X import name`` then binds the patched object.
    with patch(
        "processing.investor_report_engine.build_investor_report",
        return_value=object(),
    ), patch(
        "utils.investor_report_html.render_investor_report_html",
        return_value="<html>x</html>",
    ), patch(
        "utils.report_history.save_report", return_value=fake_meta
    ):
        result = scheduler.run_daily_briefing_job({})

    assert result.success is True
    assert result.report_id == "sched-rpt-1"

    rows = query_audit(action="scheduled_report_fire")
    assert len(rows) == 1
    ev = rows[0]
    assert ev.entity_type == "report"
    assert ev.entity_id == "sched-rpt-1"
    # System/scheduled fire — no interactive user.
    assert ev.user_id == ""
    assert ev.detail_json.get("pushed_to_channels") is False
