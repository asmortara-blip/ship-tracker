"""Tests for auth.ids.opaque_id — CLI-argument-safe opaque identifiers.

Defining property: an opaque_id is a ``secrets.token_urlsafe`` value with
the same length / alphabet / entropy, EXCEPT it never begins with ``-``.
That leading-dash guarantee is what stops the operator CLI from rejecting
``--user-id <id>`` / ``tokens revoke <token_id>`` as a bad option flag
(argparse exit 2) ~1/64 of the time — the bug this module fixes.
"""
from __future__ import annotations

import re
import secrets

import pytest

from auth.ids import opaque_id


_URLSAFE_CHARS = re.compile(r"\A[A-Za-z0-9_-]+\Z")


def test_opaque_id_never_starts_with_dash_over_many_draws() -> None:
    """The defining property — exhaustively sampled. A leading '-' would
    make the id unusable as a CLI argument."""
    for _ in range(20_000):
        assert not opaque_id(16).startswith("-")


def test_opaque_id_uses_urlsafe_alphabet() -> None:
    for _ in range(500):
        tok = opaque_id(16)
        assert _URLSAFE_CHARS.match(tok), f"non-urlsafe char in {tok!r}"


def test_opaque_id_length_matches_token_urlsafe() -> None:
    """Same length as the underlying token_urlsafe — fixed for a given
    nbytes, so no caller that pins id length is disturbed."""
    for nbytes in (12, 16, 24, 32):
        assert len(opaque_id(nbytes)) == len(secrets.token_urlsafe(nbytes))


def test_opaque_id_rejects_nonpositive_nbytes() -> None:
    """nbytes < 1 must fail fast — token_urlsafe(0) returns '' which would
    otherwise spin the dash-rejection loop forever."""
    for bad in (0, -1, -5):
        with pytest.raises(ValueError):
            opaque_id(bad)


def test_opaque_id_retries_past_a_leading_dash(monkeypatch) -> None:
    """Deterministically exercise the retry branch: the first draw starts
    with '-', so opaque_id must discard it and return the next clean one."""
    draws = iter(["-AbniezZ", "CleanToken123"])
    monkeypatch.setattr(secrets, "token_urlsafe", lambda nbytes: next(draws))
    assert opaque_id(16) == "CleanToken123"


def test_opaque_id_returns_first_clean_draw(monkeypatch) -> None:
    """When the first draw is already clean, no extra draws are consumed."""
    calls = {"n": 0}

    def _fake(nbytes):
        calls["n"] += 1
        return "GoodFirstTry"

    monkeypatch.setattr(secrets, "token_urlsafe", _fake)
    assert opaque_id(16) == "GoodFirstTry"
    assert calls["n"] == 1


def test_opaque_ids_are_distinct() -> None:
    """Sanity: still random — no accidental constant."""
    sample = {opaque_id(16) for _ in range(1000)}
    assert len(sample) == 1000
