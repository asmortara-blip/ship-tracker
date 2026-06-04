"""RBAC authorization helpers + the audit-scope security gate (rec R098).

Closes the self-asserted-admin hole: a logged-in non-admin could read the
org-wide audit log by ticking a client-side "All users (admin)" checkbox or
typing another user's id. Role is now resolved server-side and fails closed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from auth.authz import is_admin, require_role, role_of


class _U:
    def __init__(self, role):
        self.role = role


# ── role resolution (fail-closed) ──────────────────────────────────────────

def test_is_admin_true_only_for_admin_role() -> None:
    assert is_admin(_U("admin")) is True
    assert is_admin(_U("user")) is False
    assert is_admin(_U("")) is False


def test_is_admin_fails_closed_on_unknown_user() -> None:
    # No user passed and (in a test context) no streamlit session -> not admin.
    assert is_admin(None) is False
    # A user-shaped object without a role attribute is not admin.
    assert is_admin(SimpleNamespace()) is False


def test_role_of_reads_role_attribute() -> None:
    assert role_of(_U("analyst")) == "analyst"
    assert role_of(SimpleNamespace()) is None


def test_require_role_admin_satisfies_any_role() -> None:
    assert require_role("user", _U("admin")) is True
    assert require_role("anything", _U("admin")) is True


def test_require_role_exact_match_for_non_admin() -> None:
    assert require_role("user", _U("user")) is True
    assert require_role("admin", _U("user")) is False
    assert require_role("user", None) is False  # fail closed


# ── the audit-scope security gate ───────────────────────────────────────────

from ui.tab_data_health import _resolve_audit_scope  # noqa: E402


def test_non_admin_is_hard_scoped_to_self_ignoring_admin_scope() -> None:
    # Even with the "All users" checkbox POSTed True, a non-admin only sees self.
    assert _resolve_audit_scope(
        viewer_is_admin=False, self_uid="u-self", typed_id="", admin_scope=True
    ) == "u-self"


def test_non_admin_cannot_query_another_users_id() -> None:
    # Typing another user's id must be ignored for a non-admin.
    assert _resolve_audit_scope(
        viewer_is_admin=False, self_uid="u-self", typed_id="u-victim", admin_scope=False
    ) == "u-self"


def test_admin_may_broaden_to_all_users() -> None:
    assert _resolve_audit_scope(
        viewer_is_admin=True, self_uid="u-admin", typed_id="", admin_scope=True
    ) is None


def test_admin_may_query_a_specific_user() -> None:
    assert _resolve_audit_scope(
        viewer_is_admin=True, self_uid="u-admin", typed_id="u-other", admin_scope=False
    ) == "u-other"


def test_admin_default_is_self_when_no_controls_set() -> None:
    assert _resolve_audit_scope(
        viewer_is_admin=True, self_uid="u-admin", typed_id="", admin_scope=False
    ) == "u-admin"


def test_typed_id_takes_precedence_over_admin_scope_for_admin() -> None:
    assert _resolve_audit_scope(
        viewer_is_admin=True, self_uid="u-admin", typed_id="u-target", admin_scope=True
    ) == "u-target"
