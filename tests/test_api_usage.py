"""Tests for engine.api_usage — the compute-weighted API-usage ledger (R112).

Coverage:
  - classify_route: cheap default, .xlsx export, backtests, analytics,
    report-body, unknown → cheap fallback (PURE — no DB)
  - route_weight ordering (export/backtests > cheap)
  - bump_usage atomic increment accumulates (single + weighted)
  - bump_usage best-effort: non-positive weight / empty user_id no-op;
    never raises on a broken backend
  - get_api_usage reads back per-route_class counts; {} for a new user
  - check_api_quota over / under; weighted total; fail-open
  - month-keying: a bump in month A is invisible in month B
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from engine import api_usage
from engine.api_usage import (
    ROUTE_CLASS_ANALYTICS,
    ROUTE_CLASS_BACKTESTS,
    ROUTE_CLASS_CHEAP,
    ROUTE_CLASS_EXPORT,
    ROUTE_CLASS_REPORT,
    bump_usage,
    check_api_quota,
    classify_route,
    get_api_usage,
    route_weight,
)


# ─── isolated DB ───────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Redirect the SQLite state DB to a per-test tmp_path so no test
    touches the real cache/ship_tracker.db."""
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── classify_route (PURE — no DB) ─────────────────────────────────────────


def test_classify_cheap_default() -> None:
    rc, w = classify_route("/api/v1/alerts")
    assert rc == ROUTE_CLASS_CHEAP
    assert w == 1


def test_classify_unknown_path_falls_through_to_cheap() -> None:
    rc, w = classify_route("/api/v1/something/brand-new")
    assert rc == ROUTE_CLASS_CHEAP
    assert w == 1


def test_classify_xlsx_export_is_heavy() -> None:
    rc, w = classify_route("/api/v1/ports/supply-lines.xlsx")
    assert rc == ROUTE_CLASS_EXPORT
    assert w == route_weight(ROUTE_CLASS_EXPORT)
    assert w > 1


def test_classify_backtests_health() -> None:
    rc, w = classify_route("/api/v1/backtests/health")
    assert rc == ROUTE_CLASS_BACKTESTS
    assert w == route_weight(ROUTE_CLASS_BACKTESTS)


def test_classify_analytics_spillover_and_snapshots() -> None:
    rc1, _ = classify_route("/api/v1/ports/spillover-graph")
    rc2, _ = classify_route("/api/v1/ports/supply-lines/snapshots")
    rc3, _ = classify_route("/api/v1/ports/supply-lines/snapshots/2026-06-01")
    assert rc1 == rc2 == rc3 == ROUTE_CLASS_ANALYTICS


def test_classify_report_body_renders() -> None:
    rc_html, _ = classify_route("/api/v1/reports/abc123/html")
    rc_md, _ = classify_route("/api/v1/reports/abc123/markdown")
    assert rc_html == rc_md == ROUTE_CLASS_REPORT


def test_classify_plain_supply_lines_is_cheap_not_export() -> None:
    # The non-.xlsx supply-lines JSON endpoint is an ordinary read.
    rc, _ = classify_route("/api/v1/ports/supply-lines")
    assert rc == ROUTE_CLASS_CHEAP


def test_classify_empty_and_none_safe() -> None:
    assert classify_route("")[0] == ROUTE_CLASS_CHEAP
    assert classify_route(None)[0] == ROUTE_CLASS_CHEAP  # type: ignore[arg-type]


def test_weight_ordering_heavy_above_cheap() -> None:
    assert route_weight(ROUTE_CLASS_EXPORT) > route_weight(ROUTE_CLASS_CHEAP)
    assert route_weight(ROUTE_CLASS_BACKTESTS) > route_weight(ROUTE_CLASS_CHEAP)
    # An unknown class defaults to the cheap weight (never 0).
    assert route_weight("not-a-real-class") == route_weight(ROUTE_CLASS_CHEAP)


# ─── bump_usage / get_api_usage (DB) ───────────────────────────────────────


def test_bump_usage_accumulates() -> None:
    bump_usage("alice", ROUTE_CLASS_CHEAP, weight=1)
    bump_usage("alice", ROUTE_CLASS_CHEAP, weight=1)
    bump_usage("alice", ROUTE_CLASS_CHEAP, weight=1)
    usage = get_api_usage("alice")
    assert usage == {ROUTE_CLASS_CHEAP: 3}


def test_bump_usage_weighted_increment() -> None:
    bump_usage("alice", ROUTE_CLASS_EXPORT, weight=10)
    bump_usage("alice", ROUTE_CLASS_EXPORT, weight=10)
    usage = get_api_usage("alice")
    assert usage[ROUTE_CLASS_EXPORT] == 20


def test_get_api_usage_separates_route_classes() -> None:
    bump_usage("alice", ROUTE_CLASS_CHEAP, weight=1)
    bump_usage("alice", ROUTE_CLASS_CHEAP, weight=1)
    bump_usage("alice", ROUTE_CLASS_EXPORT, weight=10)
    usage = get_api_usage("alice")
    assert usage == {ROUTE_CLASS_CHEAP: 2, ROUTE_CLASS_EXPORT: 10}


def test_get_api_usage_empty_for_new_user() -> None:
    assert get_api_usage("never-seen") == {}


def test_usage_is_per_user_scoped() -> None:
    bump_usage("alice", ROUTE_CLASS_CHEAP, weight=1)
    bump_usage("alice", ROUTE_CLASS_CHEAP, weight=1)
    bump_usage("bob", ROUTE_CLASS_CHEAP, weight=1)
    assert get_api_usage("alice") == {ROUTE_CLASS_CHEAP: 2}
    assert get_api_usage("bob") == {ROUTE_CLASS_CHEAP: 1}


def test_bump_usage_nonpositive_weight_is_noop() -> None:
    bump_usage("alice", ROUTE_CLASS_CHEAP, weight=0)
    bump_usage("alice", ROUTE_CLASS_CHEAP, weight=-5)
    assert get_api_usage("alice") == {}


def test_bump_usage_empty_user_is_noop() -> None:
    bump_usage("", ROUTE_CLASS_CHEAP, weight=1)
    assert get_api_usage("") == {}


def test_bump_usage_never_raises_on_broken_backend(monkeypatch) -> None:
    """A kv_state write failure is swallowed — bump_usage NEVER raises."""
    import state.db as state_db

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(state_db, "get_connection", _boom)
    # Must not raise.
    bump_usage("alice", ROUTE_CLASS_CHEAP, weight=1)


# ─── check_api_quota (DB) ──────────────────────────────────────────────────


def test_check_api_quota_under() -> None:
    bump_usage("alice", ROUTE_CLASS_CHEAP, weight=5)
    q = check_api_quota("alice", limit=100)
    assert q == {"used": 5, "limit": 100, "over_quota": False}


def test_check_api_quota_over_uses_weighted_total() -> None:
    # 1 cheap (1) + 1 export (10) = 11 weighted, crosses a limit of 10.
    bump_usage("alice", ROUTE_CLASS_CHEAP, weight=1)
    bump_usage("alice", ROUTE_CLASS_EXPORT, weight=10)
    q = check_api_quota("alice", limit=10)
    assert q["used"] == 11
    assert q["over_quota"] is True


def test_check_api_quota_at_limit_is_over() -> None:
    bump_usage("alice", ROUTE_CLASS_CHEAP, weight=10)
    q = check_api_quota("alice", limit=10)
    assert q["over_quota"] is True  # used >= limit


def test_check_api_quota_new_user_under() -> None:
    q = check_api_quota("never-seen", limit=10)
    assert q == {"used": 0, "limit": 10, "over_quota": False}


def test_check_api_quota_fails_open_on_backend_error(monkeypatch) -> None:
    import state.db as state_db

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(state_db, "get_connection", _boom)
    q = check_api_quota("alice", limit=10)
    # Fail open: used=0, never over_quota.
    assert q["used"] == 0
    assert q["over_quota"] is False


# ─── month-keying ──────────────────────────────────────────────────────────


def test_usage_is_month_scoped(monkeypatch) -> None:
    """A bump recorded in one UTC month is invisible when reading another."""

    class _FrozenJune:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(api_usage, "datetime", _FrozenJune)
    bump_usage("alice", ROUTE_CLASS_CHEAP, weight=1)

    # Same (frozen) month reads it back.
    assert get_api_usage("alice", month="2026-06") == {ROUTE_CLASS_CHEAP: 1}
    # A different month sees nothing.
    assert get_api_usage("alice", month="2026-07") == {}


def test_check_api_quota_respects_explicit_month() -> None:
    class _FrozenJune:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

    import engine.api_usage as mod
    orig_dt = mod.datetime
    mod.datetime = _FrozenJune  # type: ignore[assignment]
    try:
        bump_usage("alice", ROUTE_CLASS_CHEAP, weight=3)
    finally:
        mod.datetime = orig_dt  # type: ignore[assignment]

    q_june = check_api_quota("alice", month="2026-06", limit=100)
    q_july = check_api_quota("alice", month="2026-07", limit=100)
    assert q_june["used"] == 3
    assert q_july["used"] == 0
