"""Per-user query scoping helpers.

Schema v7 added a nullable ``user_id`` column to each of the domain
tables (alerts, alert_rules, report_history, delivery_channels,
llm_calls) but left the load/save functions returning global data.
This module supplies the two helpers every domain module uses to wire
up per-user scoping without losing backward compatibility:

* :func:`current_user_id` resolves the active user's id from
  ``st.session_state.current_user`` when running inside Streamlit, and
  returns the empty string in every other case (Streamlit not imported,
  no session, no current_user attribute, attribute not a ``User``, …).
  It NEVER raises.

* :func:`scope_filter_sql` returns the SQL fragment that callers append
  to a ``WHERE`` clause. The fragment uses dual-set semantics —
  ``user_id = ? OR user_id = ''`` — so authenticated users see both
  their own rows AND legacy ``user_id=''`` rows. When the caller passes
  the empty string, the fragment is empty and the query returns every
  row (legacy "all rows" behaviour). This keeps the new scoping fully
  opt-in: every existing call site that does not pass a ``user_id``
  observes the legacy semantics exactly.
"""
from __future__ import annotations


def current_user_id() -> str:
    """Return the active user's ``user_id`` from Streamlit, or ``""``.

    Best-effort lookup that NEVER raises. Returns:

    * ``""`` when Streamlit is not importable (running outside the app).
    * ``""`` when ``st.session_state`` is missing or unreadable.
    * ``""`` when ``current_user`` is unset or ``None``.
    * ``""`` when ``current_user`` has no ``user_id`` attribute, or the
      attribute is not a string, or is an empty string.
    * The user's ``user_id`` string otherwise.

    The single catch-all ``except Exception`` is intentional. Streamlit
    raises ``StreamlitAPIException`` on session-state access outside a
    real ScriptRunner; a partially-mocked ``session_state`` dict may
    raise ``KeyError``; an integration test may inject a malformed
    ``current_user``. Every failure path collapses to the legacy
    "no user" return value so the calling load/save function continues
    to behave like the pre-multi-user codebase.
    """
    try:
        import streamlit as st  # local import — never required at module load
    except Exception:
        return ""
    try:
        user = st.session_state.get("current_user")  # type: ignore[attr-defined]
    except Exception:
        return ""
    if user is None:
        return ""
    try:
        uid = getattr(user, "user_id", "")
    except Exception:
        return ""
    if not isinstance(uid, str):
        return ""
    return uid


def scope_filter_sql(user_id: str) -> tuple[str, tuple]:
    """Build the dual-set SQL fragment for per-user scoping.

    When ``user_id`` is the empty string (or any falsy value), returns
    ``("", ())`` — the caller's ``WHERE`` clause is unchanged and the
    query returns every row, matching the pre-multi-user behaviour.

    When ``user_id`` is non-empty, returns
    ``("AND (user_id = ? OR user_id = '')", (user_id,))``. The OR
    against the empty string is the dual-set semantics: pre-multi-user
    rows have ``user_id=''`` and remain visible to authenticated users
    so legacy data does not vanish the day someone signs up. Callers
    append the fragment to a clause that already starts with WHERE
    (e.g. ``WHERE created_at >= ?``) — the leading ``AND`` is part of
    the fragment.

    The bound-parameter tuple is intentionally exactly one element
    long so the caller can concatenate it with their existing
    parameter tuple without thinking about how many user_id binds the
    fragment introduces.
    """
    if not user_id:
        return ("", ())
    return ("AND (user_id = ? OR user_id = '')", (user_id,))
