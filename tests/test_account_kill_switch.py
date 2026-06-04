"""Account disable/suspend kill-switch (schema v30; rec R104).

A disabled account must be unable to log in (even with the right password)
or use its API tokens, while its row + audit trail + owned data are preserved.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


def test_migration_added_is_active_column_default_active() -> None:
    from auth.users import signup
    user = signup("alice", "correct-horse-battery")
    assert user is not None
    assert user.is_active is True  # grandfathered active by default


def test_is_user_active_helpers() -> None:
    from auth.users import (
        deactivate_user,
        is_user_active,
        reactivate_user,
        signup,
    )
    u = signup("alice", "correct-horse-battery")
    assert is_user_active(u.user_id) is True
    assert deactivate_user(u.user_id) is True
    assert is_user_active(u.user_id) is False
    assert reactivate_user(u.user_id) is True
    assert is_user_active(u.user_id) is True
    # unknown user -> no row updated
    assert deactivate_user("ghost") is False


def test_disabled_account_cannot_log_in_even_with_correct_password() -> None:
    from auth.users import deactivate_user, login, reactivate_user, signup
    u = signup("alice", "correct-horse-battery")
    assert login("alice", "correct-horse-battery") is not None  # works while active

    deactivate_user(u.user_id)
    assert login("alice", "correct-horse-battery") is None  # killed despite right pw

    reactivate_user(u.user_id)
    assert login("alice", "correct-horse-battery") is not None  # restored


def test_disabled_account_tokens_are_dead() -> None:
    from auth.tokens import create_token, verify_token
    from auth.users import deactivate_user, reactivate_user, signup
    u = signup("alice", "correct-horse-battery")
    meta, raw = create_token(u.user_id, "ci-bot")
    assert verify_token(raw) == u.user_id  # valid while active

    deactivate_user(u.user_id)
    assert verify_token(raw) is None  # token dead while disabled

    reactivate_user(u.user_id)
    assert verify_token(raw) == u.user_id  # alive again


def test_token_only_user_unaffected_fail_open() -> None:
    # A token whose user_id has no users row (legacy / token-only) keeps
    # working — is_user_active fails open to True for an absent row.
    from auth.tokens import create_token, verify_token
    meta, raw = create_token("u-no-row", "legacy")
    assert verify_token(raw) == "u-no-row"


def test_get_user_reflects_active_state() -> None:
    from auth.users import deactivate_user, get_user, signup
    u = signup("alice", "correct-horse-battery")
    assert get_user(u.user_id).is_active is True
    deactivate_user(u.user_id)
    assert get_user(u.user_id).is_active is False
