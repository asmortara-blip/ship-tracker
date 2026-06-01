"""Tests for engine.alert_annotations (schema v23).

Pre-v23 the only writable field on an alert was ``acknowledged_note``
— a single string set once at acknowledgement. v23 adds an
unbounded per-alert thread that the ops team can edit and delete —
limited to the original author so cross-operator audit trails stay
intact.

This module covers:

* CRUD: add_annotation / list_annotations / edit_annotation /
  delete_annotation persistence semantics.
* Per-user scoping: alice cannot see, edit, or delete annotations
  on bob's alerts.
* Author-only mutation: a non-author returns False on edit / delete
  attempts; the row is untouched.
* count_annotations_per_alert returns batched counts in one query
  + handles the missing alert / empty input edge cases.
* Body handling: > 4000 chars silently truncates; UTF-8 / emoji
  survives the round-trip; HTML / markdown is stored VERBATIM so
  the UI's render-safe boundary is the only XSS gate.
* Defensive contract: every helper NEVER raises on bad input
  (empty body, empty user_id, missing alert_id, etc.).
"""
from __future__ import annotations

import pytest


# ─── Isolation fixture ────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Per-test SQLite isolation. Same pattern as test_alert_silences."""
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Helpers ──────────────────────────────────────────────────────────────

def _add(alert_id: str, body: str, *, user_id: str, author: str | None = None):
    """Thin wrapper so tests don't drown in keyword args."""
    from engine.alert_annotations import add_annotation
    return add_annotation(alert_id, body, user_id=user_id, author_user_id=author)


# ─── add_annotation ───────────────────────────────────────────────────────

def test_add_annotation_persists_all_fields() -> None:
    """add_annotation persists the supplied fields + auto-stamps
    annotation_id and created_at."""
    saved = _add("alert-1", "first comment", user_id="u-alice")
    assert saved is not None
    assert saved.alert_id == "alert-1"
    assert saved.body == "first comment"
    assert saved.user_id == "u-alice"
    assert saved.author_user_id == "u-alice"  # default = user_id
    assert saved.annotation_id  # auto-generated
    assert saved.created_at  # auto-stamped
    assert saved.edited_at is None  # NULL on a never-edited row


def test_add_annotation_author_defaults_to_user_id() -> None:
    """Omitted author_user_id falls back to user_id (single-user case)."""
    saved = _add("alert-1", "body", user_id="u-alice")
    assert saved is not None
    assert saved.author_user_id == "u-alice"


def test_add_annotation_distinct_author_persists() -> None:
    """Explicit author_user_id different from user_id is the multi-user
    share case — both columns persist independently."""
    saved = _add("alert-1", "body", user_id="u-alice", author="u-bob")
    assert saved is not None
    assert saved.user_id == "u-alice"
    assert saved.author_user_id == "u-bob"


def test_add_annotation_generates_uuid_like_id() -> None:
    """The annotation_id should look like a UUID hex (8-4-4-4-12 chars
    separated by dashes; 36 chars total)."""
    saved = _add("alert-1", "body", user_id="u-alice")
    assert saved is not None
    assert len(saved.annotation_id) == 36
    assert saved.annotation_id.count("-") == 4


def test_add_annotation_empty_body_returns_none() -> None:
    """Empty / whitespace-only bodies are dropped — an empty
    annotation carries no signal."""
    assert _add("alert-1", "", user_id="u-alice") is None
    assert _add("alert-1", "   ", user_id="u-alice") is None
    assert _add("alert-1", "\t\n", user_id="u-alice") is None


def test_add_annotation_empty_alert_id_returns_none() -> None:
    """An empty alert_id has no row to attach to — drop."""
    assert _add("", "body", user_id="u-alice") is None


def test_add_annotation_empty_user_id_returns_none() -> None:
    """An empty user_id means no scope — drop. The contract is hard:
    an anonymous caller cannot leave a comment that nobody can read."""
    assert _add("alert-1", "body", user_id="") is None


# ─── add_annotation NEVER raises on bad input ─────────────────────────────

def test_add_annotation_never_raises_on_bad_input() -> None:
    """The defensive contract — every helper returns None on bad
    input rather than raising. None / wrong types / impossible
    combinations all collapse to a logged warning."""
    from engine.alert_annotations import add_annotation

    # None body, None alert_id, None user_id — all caught.
    assert add_annotation("a1", None, user_id="u-alice") is None  # type: ignore[arg-type]
    assert add_annotation(None, "body", user_id="u-alice") is None  # type: ignore[arg-type]


# ─── Body length / unicode / verbatim ─────────────────────────────────────

def test_add_annotation_truncates_long_body_silently() -> None:
    """A body longer than 4000 chars is silently truncated — the
    write succeeds (operator UX) and the stored body is exactly
    4000 chars. Documented choice: truncate, do NOT reject."""
    long_body = "x" * 8000
    saved = _add("alert-1", long_body, user_id="u-alice")
    assert saved is not None
    assert len(saved.body) == 4000
    assert saved.body == "x" * 4000


def test_add_annotation_preserves_utf8_and_emoji() -> None:
    """UTF-8 / emoji survives the round-trip — SQLite TEXT is
    UTF-8 native."""
    body = "RCA in JIRA-1234 — escalated to ops team 🚨 monitoring 監視"
    saved = _add("alert-1", body, user_id="u-alice")
    assert saved is not None
    assert saved.body == body
    # And it round-trips through list_annotations too.
    from engine.alert_annotations import list_annotations
    thread = list_annotations("alert-1", user_id="u-alice")
    assert len(thread) == 1
    assert thread[0].body == body


def test_add_annotation_stores_html_markdown_verbatim() -> None:
    """HTML / markdown bodies are stored VERBATIM (no stripping,
    no rendering). The UI's render-safe boundary is the only XSS
    gate — see ui.tab_alerts._render_alert_annotations_panel."""
    body = "<script>alert('xss')</script>\n**bold** and `code` and [link](http://x)"
    saved = _add("alert-1", body, user_id="u-alice")
    assert saved is not None
    assert saved.body == body
    # No HTML escape happened on the way in.
    assert "<script>" in saved.body


# ─── list_annotations ─────────────────────────────────────────────────────

def test_list_annotations_returns_created_at_ascending() -> None:
    """list_annotations returns the thread in chronological order
    — newest at the end, matches how a conversation flows."""
    _add("alert-1", "first", user_id="u-alice")
    _add("alert-1", "second", user_id="u-alice")
    _add("alert-1", "third", user_id="u-alice")
    from engine.alert_annotations import list_annotations
    thread = list_annotations("alert-1", user_id="u-alice")
    assert len(thread) == 3
    assert [a.body for a in thread] == ["first", "second", "third"]
    # Timestamps strictly ASC.
    times = [a.created_at for a in thread]
    assert times == sorted(times)


def test_list_annotations_per_user_scoping() -> None:
    """alice cannot see bob's alert annotations even when the
    alert_id collides — the user_id filter is a hard requirement."""
    # Two users, two annotations, SAME alert_id (collision is
    # vanishingly unlikely in production because alert_id is a UUID,
    # but the test pins the user_id filter regardless).
    _add("alert-X", "alice's note", user_id="u-alice")
    _add("alert-X", "bob's note", user_id="u-bob")
    from engine.alert_annotations import list_annotations
    alice_view = list_annotations("alert-X", user_id="u-alice")
    bob_view = list_annotations("alert-X", user_id="u-bob")
    assert len(alice_view) == 1
    assert alice_view[0].body == "alice's note"
    assert len(bob_view) == 1
    assert bob_view[0].body == "bob's note"


def test_list_annotations_empty_alert_id_returns_empty() -> None:
    """An empty alert_id has no thread — return empty rather than
    leak every annotation in the table."""
    from engine.alert_annotations import list_annotations
    assert list_annotations("", user_id="u-alice") == []


def test_list_annotations_empty_user_id_returns_empty() -> None:
    """An empty user_id has no scope — return empty rather than
    leak every annotation in the table."""
    _add("alert-1", "alice's note", user_id="u-alice")
    from engine.alert_annotations import list_annotations
    assert list_annotations("alert-1", user_id="") == []


# ─── edit_annotation ──────────────────────────────────────────────────────

def test_edit_annotation_by_author_succeeds_and_sets_edited_at() -> None:
    """The original author can edit the body — the UPDATE replaces
    the body AND stamps edited_at."""
    saved = _add("alert-1", "draft", user_id="u-alice")
    assert saved is not None
    from engine.alert_annotations import edit_annotation, list_annotations
    ok = edit_annotation(
        saved.annotation_id, "revised", user_id="u-alice",
    )
    assert ok is True
    # Reload and check.
    [updated] = list_annotations("alert-1", user_id="u-alice")
    assert updated.body == "revised"
    assert updated.edited_at is not None  # stamped on edit
    assert updated.edited_at != updated.created_at  # different timestamp


def test_edit_annotation_by_non_author_returns_false() -> None:
    """Bob cannot rewrite alice's annotation even when he knows the
    annotation_id. The row stays untouched."""
    saved = _add("alert-1", "alice's draft", user_id="u-alice")
    assert saved is not None
    from engine.alert_annotations import edit_annotation, list_annotations
    # Bob attempts the edit, supplying his own user_id as both
    # owner AND author — the row's user_id matches alice, so the
    # author check never even runs; either way, False.
    ok = edit_annotation(
        saved.annotation_id, "bob's rewrite", user_id="u-bob",
    )
    assert ok is False
    [unchanged] = list_annotations("alert-1", user_id="u-alice")
    assert unchanged.body == "alice's draft"
    assert unchanged.edited_at is None  # NOT stamped


def test_edit_annotation_explicit_non_author_returns_false() -> None:
    """The multi-user-share case: the alert owner can see the row
    but did NOT write it. They cannot edit it either."""
    # alice owns the alert; bob wrote the annotation.
    saved = _add(
        "alert-1", "bob's comment", user_id="u-alice", author="u-bob",
    )
    assert saved is not None
    from engine.alert_annotations import edit_annotation, list_annotations
    # alice (the OWNER but not the AUTHOR) tries to edit — denied.
    ok = edit_annotation(
        saved.annotation_id, "alice's rewrite",
        user_id="u-alice", author_user_id="u-alice",
    )
    assert ok is False
    [unchanged] = list_annotations("alert-1", user_id="u-alice")
    assert unchanged.body == "bob's comment"
    # bob can edit — he's the author.
    ok2 = edit_annotation(
        saved.annotation_id, "bob's update",
        user_id="u-alice", author_user_id="u-bob",
    )
    assert ok2 is True


def test_edit_annotation_empty_body_returns_false() -> None:
    """Empty / whitespace bodies are rejected — the caller should
    use delete_annotation to clear a row instead."""
    saved = _add("alert-1", "original", user_id="u-alice")
    assert saved is not None
    from engine.alert_annotations import edit_annotation, list_annotations
    assert edit_annotation(
        saved.annotation_id, "", user_id="u-alice",
    ) is False
    assert edit_annotation(
        saved.annotation_id, "   ", user_id="u-alice",
    ) is False
    # Row is untouched.
    [unchanged] = list_annotations("alert-1", user_id="u-alice")
    assert unchanged.body == "original"
    assert unchanged.edited_at is None


def test_edit_annotation_unknown_id_returns_false() -> None:
    """A bogus annotation_id has no row to update — False."""
    from engine.alert_annotations import edit_annotation
    assert edit_annotation(
        "does-not-exist", "body", user_id="u-alice",
    ) is False


# ─── delete_annotation ────────────────────────────────────────────────────

def test_delete_annotation_by_author_succeeds() -> None:
    """The author can delete their own row."""
    saved = _add("alert-1", "to delete", user_id="u-alice")
    assert saved is not None
    from engine.alert_annotations import delete_annotation, list_annotations
    ok = delete_annotation(saved.annotation_id, user_id="u-alice")
    assert ok is True
    assert list_annotations("alert-1", user_id="u-alice") == []


def test_delete_annotation_by_non_author_returns_false() -> None:
    """Bob cannot delete alice's annotation. The row survives."""
    saved = _add("alert-1", "alice's note", user_id="u-alice")
    assert saved is not None
    from engine.alert_annotations import delete_annotation, list_annotations
    ok = delete_annotation(saved.annotation_id, user_id="u-bob")
    assert ok is False
    # Row still there.
    assert len(list_annotations("alert-1", user_id="u-alice")) == 1


def test_delete_annotation_explicit_non_author_returns_false() -> None:
    """Owner != author case: owner cannot delete a comment they did
    not write."""
    saved = _add(
        "alert-1", "bob's comment", user_id="u-alice", author="u-bob",
    )
    assert saved is not None
    from engine.alert_annotations import delete_annotation, list_annotations
    # alice (owner, not author) cannot delete.
    ok = delete_annotation(
        saved.annotation_id, user_id="u-alice", author_user_id="u-alice",
    )
    assert ok is False
    assert len(list_annotations("alert-1", user_id="u-alice")) == 1
    # bob (author) can.
    ok2 = delete_annotation(
        saved.annotation_id, user_id="u-alice", author_user_id="u-bob",
    )
    assert ok2 is True


def test_delete_annotation_unknown_id_returns_false() -> None:
    """Unknown annotation_id → False (no row to delete)."""
    from engine.alert_annotations import delete_annotation
    assert delete_annotation("does-not-exist", user_id="u-alice") is False


def test_delete_annotation_empty_id_returns_false() -> None:
    """Empty annotation_id collapses to False before any DB hit."""
    from engine.alert_annotations import delete_annotation
    assert delete_annotation("", user_id="u-alice") is False


# ─── count_annotations_per_alert ──────────────────────────────────────────

def test_count_annotations_per_alert_returns_counts_per_id() -> None:
    """The batch counter returns one entry per alert_id that has
    annotations — alerts with zero are absent (callers default
    to 0 on missing key)."""
    _add("alert-A", "first", user_id="u-alice")
    _add("alert-A", "second", user_id="u-alice")
    _add("alert-A", "third", user_id="u-alice")
    _add("alert-B", "lone comment", user_id="u-alice")
    from engine.alert_annotations import count_annotations_per_alert
    counts = count_annotations_per_alert(
        ["alert-A", "alert-B", "alert-C-empty"], user_id="u-alice",
    )
    assert counts["alert-A"] == 3
    assert counts["alert-B"] == 1
    assert "alert-C-empty" not in counts  # zero → absent


def test_count_annotations_per_alert_empty_input_returns_empty() -> None:
    """Empty alert_ids → empty result dict (no DB hit)."""
    from engine.alert_annotations import count_annotations_per_alert
    assert count_annotations_per_alert([], user_id="u-alice") == {}


def test_count_annotations_per_alert_per_user_scoping() -> None:
    """alice's count for an alert_id excludes bob's notes on the
    same alert_id (collision scenario again)."""
    _add("alert-X", "alice note 1", user_id="u-alice")
    _add("alert-X", "alice note 2", user_id="u-alice")
    _add("alert-X", "bob note", user_id="u-bob")
    from engine.alert_annotations import count_annotations_per_alert
    alice_counts = count_annotations_per_alert(["alert-X"], user_id="u-alice")
    bob_counts = count_annotations_per_alert(["alert-X"], user_id="u-bob")
    assert alice_counts == {"alert-X": 2}
    assert bob_counts == {"alert-X": 1}


def test_count_annotations_per_alert_empty_user_id_returns_empty() -> None:
    """Empty user_id has no scope → empty result rather than leaking."""
    _add("alert-X", "alice's note", user_id="u-alice")
    from engine.alert_annotations import count_annotations_per_alert
    assert count_annotations_per_alert(["alert-X"], user_id="") == {}
