"""auth — authentication for the Streamlit app.

This package contains two layers:

- ``auth.gate`` — the legacy single-shared-password gate. Still the
  default. Reads ``APP_PASSWORD_HASH`` / ``APP_PASSWORD_SALT`` from env
  vars (or ``st.secrets``); when not configured, runs in open mode.
- ``auth.users`` — the minimum-viable multi-user identity layer (v7
  schema). A ``users`` table backs ``signup`` / ``login`` /
  ``get_user`` / ``count_users``. The new entry point
  ``require_auth_with_users`` prefers users-table login when at least
  one user exists, and falls back to the single-password gate
  otherwise — so adopting multi-user auth is non-invasive.

Per-user data scoping (filtering domain queries by ``user_id``) is
intentionally left for a follow-up. Every domain table already has the
column from the v7 migration, ready for that work.
"""
from auth.gate import (
    AuthState,
    generate_password_hash,
    is_authenticated,
    logout,
    require_auth,
    require_auth_with_users,
)
from auth.users import (
    User,
    count_users,
    get_user,
    login,
    signup,
)

__all__ = [
    "AuthState",
    "User",
    "count_users",
    "generate_password_hash",
    "get_user",
    "is_authenticated",
    "login",
    "logout",
    "require_auth",
    "require_auth_with_users",
    "signup",
]
