"""Typed session/state helpers for the Ship Tracker Streamlit app.

Modules:
    session   — Typed ``SessionState`` dataclass backed by ``st.session_state``.

Phase 1 introduces session; scenarios and filters land in Phase 3.
"""
from state.session import SessionState, get_session

__all__ = ["SessionState", "get_session"]
