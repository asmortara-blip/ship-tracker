"""Tests for the R118 session idle + absolute-lifetime timeout (auth/gate.py).

Two layers:

1. The pure decision function ``_session_expiry_check(authed_at, last_seen,
   now) -> (expired, reason)`` — exhaustively unit-tested with no Streamlit
   dependency. This is the load-bearing logic.

2. The session-state helpers (``stamp_session_start``, ``_enforce_session_
   lifetime``) driven against the ``mock_streamlit`` session_state stub, to
   confirm the gate's stamp/refresh/teardown wiring behaves.

Fail-modes asserted: fresh → allowed + refreshed; idle → expired; absolute
→ expired; no stamps → stamped + allowed (NOT expired); unparseable →
expired (fail-closed).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from auth import gate as auth_gate
from auth.gate import (
    ABSOLUTE_LIFETIME_HOURS,
    IDLE_TIMEOUT_MINUTES,
    _SESSION_AUTHED_AT_KEY,
    _SESSION_LAST_SEEN_KEY,
    _USER_SCOPED_SESSION_KEYS,
    _enforce_session_lifetime,
    _expire_multiuser_session,
    _session_expiry_check,
    stamp_session_start,
)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ── Constants are sane module-level knobs ─────────────────────────────────

def test_timeout_constants_are_positive() -> None:
    assert IDLE_TIMEOUT_MINUTES > 0
    assert ABSOLUTE_LIFETIME_HOURS > 0


# ── Pure decision: fresh session ──────────────────────────────────────────

def test_fresh_session_is_not_expired() -> None:
    now = datetime.now(timezone.utc)
    expired, reason = _session_expiry_check(_iso(now), _iso(now), now)
    assert expired is False
    assert reason == "ok"


def test_recent_activity_just_under_idle_window_ok() -> None:
    now = datetime.now(timezone.utc)
    authed = _iso(now - timedelta(hours=1))
    last_seen = _iso(now - timedelta(minutes=IDLE_TIMEOUT_MINUTES - 1))
    expired, reason = _session_expiry_check(authed, last_seen, now)
    assert expired is False
    assert reason == "ok"


# ── Pure decision: idle timeout ───────────────────────────────────────────

def test_idle_session_is_expired() -> None:
    now = datetime.now(timezone.utc)
    authed = _iso(now - timedelta(minutes=IDLE_TIMEOUT_MINUTES + 5))
    last_seen = _iso(now - timedelta(minutes=IDLE_TIMEOUT_MINUTES + 5))
    expired, reason = _session_expiry_check(authed, last_seen, now)
    assert expired is True
    assert reason == "idle"


def test_idle_boundary_just_over_window_expires() -> None:
    now = datetime.now(timezone.utc)
    # authed recently so the absolute cap doesn't fire first
    authed = _iso(now - timedelta(minutes=IDLE_TIMEOUT_MINUTES + 2))
    last_seen = _iso(now - timedelta(minutes=IDLE_TIMEOUT_MINUTES, seconds=1))
    expired, reason = _session_expiry_check(authed, last_seen, now)
    assert expired is True
    assert reason == "idle"


# ── Pure decision: absolute lifetime cap ──────────────────────────────────

def test_absolute_expired_session_is_bounced() -> None:
    now = datetime.now(timezone.utc)
    authed = _iso(now - timedelta(hours=ABSOLUTE_LIFETIME_HOURS + 1))
    # last_seen recent → idle would NOT fire; only the absolute cap does.
    last_seen = _iso(now - timedelta(minutes=1))
    expired, reason = _session_expiry_check(authed, last_seen, now)
    assert expired is True
    assert reason == "absolute"


def test_absolute_cap_checked_before_idle() -> None:
    """An old session that is also idle reports the absolute reason."""
    now = datetime.now(timezone.utc)
    authed = _iso(now - timedelta(hours=ABSOLUTE_LIFETIME_HOURS + 5))
    last_seen = _iso(now - timedelta(hours=ABSOLUTE_LIFETIME_HOURS + 5))
    expired, reason = _session_expiry_check(authed, last_seen, now)
    assert expired is True
    assert reason == "absolute"


# ── Pure decision: no stamps (legacy/edge) → allow + caller stamps ────────

def test_no_stamps_is_not_expired() -> None:
    now = datetime.now(timezone.utc)
    expired, reason = _session_expiry_check(None, None, now)
    assert expired is False
    assert reason == "no_stamps"


def test_empty_string_stamps_are_treated_as_no_stamps() -> None:
    now = datetime.now(timezone.utc)
    expired, reason = _session_expiry_check("", "", now)
    assert expired is False
    assert reason == "no_stamps"


# ── Pure decision: unparseable stamps → expired (fail-closed) ─────────────

def test_unparseable_last_seen_expires() -> None:
    now = datetime.now(timezone.utc)
    expired, reason = _session_expiry_check(_iso(now), "not-a-timestamp", now)
    assert expired is True
    assert reason == "unparseable_last_seen"


def test_unparseable_authed_at_expires() -> None:
    now = datetime.now(timezone.utc)
    expired, reason = _session_expiry_check("garbage", _iso(now), now)
    assert expired is True
    assert reason == "unparseable_authed_at"


def test_partial_stamp_present_but_unparseable_other_missing_expires() -> None:
    """Only one stamp present, and it's corrupt → fail closed."""
    now = datetime.now(timezone.utc)
    expired, reason = _session_expiry_check("garbage", None, now)
    assert expired is True
    assert reason == "unparseable_authed_at"


# ── Pure decision: tz-naive stamps assumed UTC ────────────────────────────

def test_naive_timestamps_assumed_utc_and_fresh_ok() -> None:
    now = datetime.now(timezone.utc)
    naive_now = now.replace(tzinfo=None)
    expired, reason = _session_expiry_check(
        naive_now.isoformat(), naive_now.isoformat(), now
    )
    assert expired is False
    assert reason == "ok"


def test_check_never_raises_on_weird_types() -> None:
    """Non-string junk must not blow up the pure function."""
    now = datetime.now(timezone.utc)
    for bad in (123, 4.5, object(), [], {}):
        expired, reason = _session_expiry_check(bad, bad, now)
        # Both "present" stamps fail the isinstance(str) check → treated as
        # absent → no_stamps (not a crash).
        assert reason == "no_stamps"
        assert expired is False


def test_naive_now_is_tolerated() -> None:
    """A tz-naive ``now`` is coerced to UTC rather than raising."""
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    authed = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    expired, reason = _session_expiry_check(authed, authed, now_naive)
    assert expired is False
    assert reason == "ok"


# ── Helper layer: stamp + enforce against the session_state stub ──────────

def test_stamp_session_start_sets_both_keys(mock_streamlit) -> None:
    import streamlit as st
    stamp_session_start(st)
    assert _SESSION_AUTHED_AT_KEY in st.session_state
    assert _SESSION_LAST_SEEN_KEY in st.session_state
    # both parse as ISO datetimes
    datetime.fromisoformat(st.session_state[_SESSION_AUTHED_AT_KEY])
    datetime.fromisoformat(st.session_state[_SESSION_LAST_SEEN_KEY])


def test_enforce_fresh_session_allows_and_refreshes_last_seen(mock_streamlit) -> None:
    import streamlit as st
    now = datetime.now(timezone.utc)
    old_last_seen = _iso(now - timedelta(minutes=5))
    st.session_state[_SESSION_AUTHED_AT_KEY] = _iso(now - timedelta(minutes=10))
    st.session_state[_SESSION_LAST_SEEN_KEY] = old_last_seen

    ok = _enforce_session_lifetime(st)
    assert ok is True
    # last_seen was refreshed to (approximately) now — strictly newer than old
    new_last_seen = st.session_state[_SESSION_LAST_SEEN_KEY]
    assert datetime.fromisoformat(new_last_seen) > datetime.fromisoformat(old_last_seen)


def test_enforce_idle_session_expires_and_clears(mock_streamlit) -> None:
    import streamlit as st

    class _U:
        user_id = "u1"

    now = datetime.now(timezone.utc)
    st.session_state["current_user"] = _U()
    st.session_state[_SESSION_AUTHED_AT_KEY] = _iso(now - timedelta(minutes=IDLE_TIMEOUT_MINUTES + 10))
    st.session_state[_SESSION_LAST_SEEN_KEY] = _iso(now - timedelta(minutes=IDLE_TIMEOUT_MINUTES + 10))

    ok = _enforce_session_lifetime(st)
    assert ok is False
    # session torn down: current_user gone + stamps cleared
    assert st.session_state.get("current_user") in (None,)
    assert _SESSION_AUTHED_AT_KEY not in st.session_state
    assert _SESSION_LAST_SEEN_KEY not in st.session_state


def test_enforce_absolute_session_expires(mock_streamlit) -> None:
    import streamlit as st
    now = datetime.now(timezone.utc)
    st.session_state["current_user"] = object()
    st.session_state[_SESSION_AUTHED_AT_KEY] = _iso(now - timedelta(hours=ABSOLUTE_LIFETIME_HOURS + 2))
    st.session_state[_SESSION_LAST_SEEN_KEY] = _iso(now - timedelta(minutes=1))

    ok = _enforce_session_lifetime(st)
    assert ok is False


def test_enforce_no_stamps_stamps_now_and_allows(mock_streamlit) -> None:
    """A user session with no lifetime stamps is allowed and stamped — this
    is the legacy/edge case that must NOT break existing sessions/tests."""
    import streamlit as st
    st.session_state["current_user"] = object()
    assert _SESSION_AUTHED_AT_KEY not in st.session_state

    ok = _enforce_session_lifetime(st)
    assert ok is True
    # now stamped, so the next render starts the window
    assert _SESSION_AUTHED_AT_KEY in st.session_state
    assert _SESSION_LAST_SEEN_KEY in st.session_state


def test_enforce_unparseable_expires(mock_streamlit) -> None:
    import streamlit as st
    st.session_state["current_user"] = object()
    st.session_state[_SESSION_AUTHED_AT_KEY] = "totally-broken"
    st.session_state[_SESSION_LAST_SEEN_KEY] = "also-broken"

    ok = _enforce_session_lifetime(st)
    assert ok is False
    assert st.session_state.get("current_user") in (None,)


def test_expiry_purges_all_user_scoped_session_caches(mock_streamlit) -> None:
    # R118 review fix: expiry must purge the WHOLE user-scoped surface, not just
    # current_user — else the next user on the same browser inherits A's data.
    import streamlit as st
    st.session_state["current_user"] = {"username": "alice"}
    st.session_state[_SESSION_AUTHED_AT_KEY] = "2026-06-07T00:00:00+00:00"
    st.session_state[_SESSION_LAST_SEEN_KEY] = "2026-06-07T00:00:00+00:00"
    st.session_state["portfolio_positions"] = [{"ticker": "ZIM", "shares": 100}]
    st.session_state["user_alerts"] = ["A's alert rule"]
    st.session_state["pending_mfa_secret"] = "A-pending-secret"
    st.session_state["nav_section"] = "disruption_alpha"  # app-global, must SURVIVE

    _expire_multiuser_session(st)

    assert "current_user" not in st.session_state
    assert _SESSION_AUTHED_AT_KEY not in st.session_state
    assert _SESSION_LAST_SEEN_KEY not in st.session_state
    for key in _USER_SCOPED_SESSION_KEYS:
        assert key not in st.session_state, f"{key} leaked into the next session"
    # An app-global key (NOT user-scoped) is left untouched.
    assert st.session_state.get("nav_section") == "disruption_alpha"
