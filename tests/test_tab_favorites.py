"""Tests for ``state.tab_favorites`` — per-user pinned-tab helpers.

The defining properties under test:

  * :func:`get_pinned_tabs` returns ``[]`` for a brand-new user (NEVER
    raises).
  * :func:`pin_tab` appends to the list and is idempotent (re-pinning
    the same module yields one entry).
  * :func:`unpin_tab` removes the entry; unpinning an unknown module is
    a no-op that returns ``True``.
  * :func:`reorder_pinned_tabs` only accepts permutations of the
    user's current pinned-list (rejects adds, removes, duplicates, and
    unknown modules).
  * Per-user isolation: alice's pins never leak into bob's.
  * :func:`get_pinned_tabs` preserves stored order (NOT alphabetical).
  * Every helper swallows bad input and returns ``False`` / ``[]``
    rather than raising.
  * A corrupted ``extras['pinned_tabs']`` blob (non-list, non-string
    entries) degrades silently to ``[]``.
  * Full pin/unpin round-trip leaves the user back at the starting
    state.

All persistence routes through :class:`auth.settings.UserSettings`'
``extras`` dict — there is no schema bump and no dedicated table.
"""
from __future__ import annotations

import pytest


# ─── Fixture: isolate persistence to a tmp file per test ────────────────────


@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Redirect the SQLite state DB to a per-test tmp_path so no test
    touches the real cache/ship_tracker.db. Mirrors the pattern from
    ``tests/test_user_settings.py``."""
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── get_pinned_tabs: empty for new users ─────────────────────────────────


def test_get_pinned_tabs_empty_for_new_user() -> None:
    """A user that has never pinned anything reads back an empty list."""
    from state.tab_favorites import get_pinned_tabs

    assert get_pinned_tabs(user_id="u-new") == []


def test_get_pinned_tabs_empty_for_missing_user_id() -> None:
    """The empty-string user_id (logged-out caller) returns ``[]``."""
    from state.tab_favorites import get_pinned_tabs

    assert get_pinned_tabs(user_id="") == []
    assert get_pinned_tabs(user_id=None) == []  # type: ignore[arg-type]


# ─── pin_tab: append + idempotent ─────────────────────────────────────────


def test_pin_tab_adds_to_list() -> None:
    from state.tab_favorites import get_pinned_tabs, pin_tab

    assert pin_tab("ui.tab_alerts", user_id="u-alice") is True
    assert get_pinned_tabs(user_id="u-alice") == ["ui.tab_alerts"]


def test_pin_tab_idempotent() -> None:
    """Pinning the same module twice still results in one entry."""
    from state.tab_favorites import get_pinned_tabs, pin_tab

    assert pin_tab("ui.tab_alerts", user_id="u-alice") is True
    assert pin_tab("ui.tab_alerts", user_id="u-alice") is True
    assert get_pinned_tabs(user_id="u-alice") == ["ui.tab_alerts"]


def test_pin_tab_appends_to_end() -> None:
    """Multiple distinct pins arrive in pin-order (NOT alphabetical)."""
    from state.tab_favorites import get_pinned_tabs, pin_tab

    pin_tab("ui.tab_portfolio", user_id="u-alice")
    pin_tab("ui.tab_alerts",    user_id="u-alice")
    pin_tab("ui.tab_backtest",  user_id="u-alice")

    assert get_pinned_tabs(user_id="u-alice") == [
        "ui.tab_portfolio",
        "ui.tab_alerts",
        "ui.tab_backtest",
    ]


# ─── unpin_tab: remove + idempotent ───────────────────────────────────────


def test_unpin_tab_removes_entry() -> None:
    from state.tab_favorites import get_pinned_tabs, pin_tab, unpin_tab

    pin_tab("ui.tab_alerts",    user_id="u-alice")
    pin_tab("ui.tab_portfolio", user_id="u-alice")
    assert unpin_tab("ui.tab_alerts", user_id="u-alice") is True
    assert get_pinned_tabs(user_id="u-alice") == ["ui.tab_portfolio"]


def test_unpin_unknown_module_is_noop_success() -> None:
    """Unpinning a module that was never pinned is an idempotent success."""
    from state.tab_favorites import get_pinned_tabs, unpin_tab

    assert unpin_tab("ui.tab_never_existed", user_id="u-alice") is True
    assert get_pinned_tabs(user_id="u-alice") == []


# ─── reorder_pinned_tabs: strict permutation ──────────────────────────────


def test_reorder_pinned_tabs_sets_ordered_list() -> None:
    from state.tab_favorites import (
        get_pinned_tabs, pin_tab, reorder_pinned_tabs,
    )

    pin_tab("ui.tab_alerts",    user_id="u-alice")
    pin_tab("ui.tab_portfolio", user_id="u-alice")
    pin_tab("ui.tab_backtest",  user_id="u-alice")

    new_order = ["ui.tab_backtest", "ui.tab_alerts", "ui.tab_portfolio"]
    assert reorder_pinned_tabs(new_order, user_id="u-alice") is True
    assert get_pinned_tabs(user_id="u-alice") == new_order


def test_reorder_rejects_unknown_module() -> None:
    """Reorder must contain ONLY currently-pinned modules — no adds."""
    from state.tab_favorites import (
        get_pinned_tabs, pin_tab, reorder_pinned_tabs,
    )

    pin_tab("ui.tab_alerts",    user_id="u-alice")
    pin_tab("ui.tab_portfolio", user_id="u-alice")

    rejected = ["ui.tab_alerts", "ui.tab_portfolio", "ui.tab_unknown"]
    assert reorder_pinned_tabs(rejected, user_id="u-alice") is False
    # Original order untouched.
    assert get_pinned_tabs(user_id="u-alice") == [
        "ui.tab_alerts", "ui.tab_portfolio",
    ]


def test_reorder_rejects_partial_list() -> None:
    """Reorder must be a full permutation — dropping a pin is rejected."""
    from state.tab_favorites import (
        get_pinned_tabs, pin_tab, reorder_pinned_tabs,
    )

    pin_tab("ui.tab_alerts",    user_id="u-alice")
    pin_tab("ui.tab_portfolio", user_id="u-alice")

    # One short: rejected.
    assert reorder_pinned_tabs(
        ["ui.tab_alerts"], user_id="u-alice"
    ) is False
    assert get_pinned_tabs(user_id="u-alice") == [
        "ui.tab_alerts", "ui.tab_portfolio",
    ]


def test_reorder_rejects_duplicates() -> None:
    """A reorder containing duplicates is rejected wholesale."""
    from state.tab_favorites import (
        get_pinned_tabs, pin_tab, reorder_pinned_tabs,
    )

    pin_tab("ui.tab_alerts",    user_id="u-alice")
    pin_tab("ui.tab_portfolio", user_id="u-alice")

    assert reorder_pinned_tabs(
        ["ui.tab_alerts", "ui.tab_alerts"], user_id="u-alice"
    ) is False
    assert get_pinned_tabs(user_id="u-alice") == [
        "ui.tab_alerts", "ui.tab_portfolio",
    ]


# ─── Per-user isolation ────────────────────────────────────────────────────


def test_per_user_isolation() -> None:
    """Alice's pins never leak into bob's."""
    from state.tab_favorites import get_pinned_tabs, pin_tab

    pin_tab("ui.tab_alerts",    user_id="u-alice")
    pin_tab("ui.tab_portfolio", user_id="u-bob")

    assert get_pinned_tabs(user_id="u-alice") == ["ui.tab_alerts"]
    assert get_pinned_tabs(user_id="u-bob")   == ["ui.tab_portfolio"]


# ─── Order preservation ───────────────────────────────────────────────────


def test_get_pinned_tabs_preserves_stored_order() -> None:
    """A user who pins in non-alphabetical order reads back in that order."""
    from state.tab_favorites import get_pinned_tabs, pin_tab

    # Pin in deliberately non-alphabetical order:
    pin_tab("ui.tab_zoo",  user_id="u-alice")
    pin_tab("ui.tab_apple", user_id="u-alice")
    pin_tab("ui.tab_mango", user_id="u-alice")

    assert get_pinned_tabs(user_id="u-alice") == [
        "ui.tab_zoo", "ui.tab_apple", "ui.tab_mango",
    ]


# ─── Bad input never raises ────────────────────────────────────────────────


def test_pin_tab_bad_input_never_raises() -> None:
    """Every bad-input path returns ``False``, NEVER raises."""
    from state.tab_favorites import pin_tab

    # Non-string module name.
    assert pin_tab(None, user_id="u-alice") is False  # type: ignore[arg-type]
    assert pin_tab(123, user_id="u-alice") is False   # type: ignore[arg-type]
    # Empty string.
    assert pin_tab("", user_id="u-alice") is False
    # Empty user_id.
    assert pin_tab("ui.tab_alerts", user_id="") is False


def test_unpin_tab_bad_input_never_raises() -> None:
    from state.tab_favorites import unpin_tab

    assert unpin_tab(None, user_id="u-alice") is False  # type: ignore[arg-type]
    assert unpin_tab("", user_id="u-alice") is False
    assert unpin_tab("ui.tab_alerts", user_id="") is False


def test_reorder_bad_input_never_raises() -> None:
    from state.tab_favorites import reorder_pinned_tabs

    # Non-list.
    assert reorder_pinned_tabs("not-a-list", user_id="u-alice") is False  # type: ignore[arg-type]
    # Non-string entries.
    assert reorder_pinned_tabs([1, 2, 3], user_id="u-alice") is False     # type: ignore[list-item]
    # Empty user_id.
    assert reorder_pinned_tabs([], user_id="") is False


# ─── Corrupted extras blob ─────────────────────────────────────────────────


def test_corrupted_extras_returns_empty_silently() -> None:
    """A non-list at ``extras['pinned_tabs']`` degrades to ``[]``."""
    from auth.settings import UserSettings, save_settings
    from state.tab_favorites import get_pinned_tabs

    # Persist a garbage shape directly (string instead of list).
    save_settings(UserSettings(
        user_id="u-corrupt",
        extras={"pinned_tabs": "this-is-not-a-list"},
    ))
    assert get_pinned_tabs(user_id="u-corrupt") == []


def test_corrupted_extras_with_mixed_types_filters_silently() -> None:
    """Non-string entries inside the pinned-list are silently filtered."""
    from auth.settings import UserSettings, save_settings
    from state.tab_favorites import get_pinned_tabs

    save_settings(UserSettings(
        user_id="u-mixed",
        extras={"pinned_tabs": [
            "ui.tab_alerts",   # kept
            None,              # dropped
            42,                # dropped
            "",                # dropped (empty)
            "ui.tab_portfolio",  # kept
            {"nested": "dict"},  # dropped
        ]},
    ))
    assert get_pinned_tabs(user_id="u-mixed") == [
        "ui.tab_alerts", "ui.tab_portfolio",
    ]


def test_corrupted_extras_with_duplicates_dedupes() -> None:
    """Defensive de-dup at read time even though write-time is idempotent."""
    from auth.settings import UserSettings, save_settings
    from state.tab_favorites import get_pinned_tabs

    save_settings(UserSettings(
        user_id="u-dupes",
        extras={"pinned_tabs": [
            "ui.tab_alerts", "ui.tab_alerts", "ui.tab_portfolio",
        ]},
    ))
    assert get_pinned_tabs(user_id="u-dupes") == [
        "ui.tab_alerts", "ui.tab_portfolio",
    ]


# ─── pin/unpin round-trip ─────────────────────────────────────────────────


def test_pin_unpin_roundtrip_leaves_starting_state() -> None:
    from state.tab_favorites import get_pinned_tabs, pin_tab, unpin_tab

    assert get_pinned_tabs(user_id="u-rt") == []
    pin_tab("ui.tab_alerts", user_id="u-rt")
    pin_tab("ui.tab_portfolio", user_id="u-rt")
    assert get_pinned_tabs(user_id="u-rt") == [
        "ui.tab_alerts", "ui.tab_portfolio",
    ]
    unpin_tab("ui.tab_alerts", user_id="u-rt")
    unpin_tab("ui.tab_portfolio", user_id="u-rt")
    assert get_pinned_tabs(user_id="u-rt") == []


# ─── is_pinned convenience query ──────────────────────────────────────────


def test_is_pinned_query() -> None:
    from state.tab_favorites import is_pinned, pin_tab

    assert is_pinned("ui.tab_alerts", user_id="u-alice") is False
    pin_tab("ui.tab_alerts", user_id="u-alice")
    assert is_pinned("ui.tab_alerts", user_id="u-alice") is True
    assert is_pinned("ui.tab_portfolio", user_id="u-alice") is False


# ─── Returned list is a copy ──────────────────────────────────────────────


def test_get_pinned_tabs_returns_fresh_copy() -> None:
    """Mutating the returned list does NOT touch stored state."""
    from state.tab_favorites import get_pinned_tabs, pin_tab

    pin_tab("ui.tab_alerts", user_id="u-alice")
    pins = get_pinned_tabs(user_id="u-alice")
    pins.append("ui.tab_hacked")
    pins.clear()
    # Fresh read should be unaffected.
    assert get_pinned_tabs(user_id="u-alice") == ["ui.tab_alerts"]
