"""Server-side role authorization helpers.

Ship models a per-user ``role`` (``User.role``, default ``"user"``; see
``auth.users``) but historically never *enforced* it — privileged surfaces
(e.g. the org-wide audit-log viewer) gated only on a client-side checkbox, so
any authenticated user could self-assert admin scope.

These helpers resolve the active user's role **server-side** and **fail
closed**: an unknown, unauthenticated, or role-less user is never privileged.
They are import-safe from non-UI callers (CLI / tests) — the Streamlit session
is resolved lazily and absence simply yields "not privileged".
"""

from __future__ import annotations

from typing import Optional

ADMIN_ROLE = "admin"


def _session_user():
    """Return the authenticated ``User`` from the Streamlit session, or None.

    Streamlit is imported lazily so this module is usable from CLI/test code
    with no Streamlit context. Any failure resolves to ``None`` (fail closed).
    """
    try:
        import streamlit as st
    except Exception:
        return None
    try:
        return st.session_state.get("current_user")
    except Exception:
        return None


def role_of(user=None) -> Optional[str]:
    """Role of ``user`` (or the active session user when None). None if unknown."""
    if user is None:
        user = _session_user()
    return getattr(user, "role", None)


def is_admin(user=None) -> bool:
    """True iff the resolved user holds the admin role.

    Fail-closed: an unauthenticated / unknown / role-less user is NOT admin.
    """
    return role_of(user) == ADMIN_ROLE


def require_role(role: str, user=None) -> bool:
    """True iff the resolved user satisfies ``role``.

    Admins satisfy any role. Fail-closed when the role cannot be resolved.
    """
    actual = role_of(user)
    if actual is None:
        return False
    if actual == ADMIN_ROLE:
        return True
    return actual == role
