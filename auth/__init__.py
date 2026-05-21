"""auth — single-password authentication gate for the Streamlit app.

This is intentionally NOT a multi-user system. It is a single shared
password that gates access to the entire app. Multi-user (per-user
data scoping, foreign-key `user_id` columns on every persistence
table, etc.) is a much larger architectural decision and is out of
scope here. See ``docs/AUTH.md`` for details.
"""
from auth.gate import (
    AuthState,
    generate_password_hash,
    is_authenticated,
    logout,
    require_auth,
)

__all__ = [
    "AuthState",
    "generate_password_hash",
    "is_authenticated",
    "logout",
    "require_auth",
]
