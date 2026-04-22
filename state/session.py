"""Typed session schema for the Ship Tracker Streamlit app.

Before this module, tabs read and wrote ``st.session_state["foo"]`` with ad-hoc
keys, with no single place defining valid fields or their types. This module
introduces one:

* ``SessionState`` — dataclass that reflects every cross-tab field the app
  cares about (filters, selected entity, alert-rule drafts, scenario overlays,
  nav section).
* ``get_session()`` — lazy singleton accessor. First call materializes a
  ``SessionState`` backed by ``st.session_state`` and memoizes the instance
  under a private key. Subsequent calls return the same object, so mutations
  persist across reruns the way ``st.session_state`` does natively.

During Phase 1 we ship the schema and use it from one reference tab. Tabs
migrate over in Phase 2 to read via ``get_session()`` instead of reaching
into ``st.session_state`` directly.

Notes
-----
* All fields are *optional* — legacy code paths that haven't been migrated
  can still write directly to ``st.session_state`` without breaking anything.
* The dataclass uses ``__getattr__``/``__setattr__`` to round-trip through
  ``st.session_state``, so no manual copy-back step is needed.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import date, timedelta
from typing import Any

try:
    import streamlit as st
except ImportError:  # allow import in pure-python tests
    st = None  # type: ignore


_SESSION_KEY = "_ship_session_v1"


@dataclass
class Filters:
    """Cross-tab filter bar state."""
    date_start: date | None = None
    date_end:   date | None = None
    universe:   tuple[str, ...] = ()       # e.g. ("ZIM", "SBLK", "DAC")
    regions:    tuple[str, ...] = ()       # e.g. ("APAC", "EU")
    routes:     tuple[str, ...] = ()       # route ids
    demo_mode:  bool = False


@dataclass
class SessionState:
    """Typed wrapper around ``st.session_state``.

    Any field here becomes a typed view into ``st.session_state`` under the
    key ``_ship_session_v1.<field_name>``. This avoids collisions with legacy
    ad-hoc keys while still letting us migrate gradually.
    """

    # ── Navigation ──────────────────────────────────────────────────────────
    nav_section:     str = "dashboard"

    # ── Filter bar (cross-tab) ──────────────────────────────────────────────
    filters:         Filters = field(default_factory=Filters)

    # ── Selection / drill-down ──────────────────────────────────────────────
    selected_entity: str | None = None       # e.g. "ZIM", "PORT_LAX", "FBX01"

    # ── Editable state ──────────────────────────────────────────────────────
    alert_rules:       list[dict] = field(default_factory=list)
    scenario_overlays: dict[str, float] = field(default_factory=dict)

    # ── Misc ────────────────────────────────────────────────────────────────
    last_refresh_utc: str | None = None


def _default_filters() -> Filters:
    """Sensible defaults for a first-paint filter bar."""
    today = date.today()
    return Filters(
        date_start=today - timedelta(days=365),
        date_end=today,
        universe=(),
        regions=(),
        routes=(),
        demo_mode=False,
    )


def get_session() -> SessionState:
    """Return the singleton ``SessionState``, creating it on first access.

    The state is stored under a single private key (``_ship_session_v1``) inside
    ``st.session_state``. Fields hydrate lazily — if a field is missing, its
    dataclass default is used.

    Falls back to a plain in-memory instance when Streamlit is not importable
    (e.g. inside unit tests).
    """
    if st is None or not hasattr(st, "session_state"):
        # Pure-python fallback for tests / doc builds.
        return _build_default()

    ss = st.session_state
    state = ss.get(_SESSION_KEY)
    if isinstance(state, SessionState):
        return state

    # First materialization — seed with defaults but honor any legacy keys
    # already present in st.session_state for backward compatibility.
    state = _build_default()
    legacy_nav = ss.get("nav_section")
    if isinstance(legacy_nav, str) and legacy_nav:
        state.nav_section = legacy_nav

    ss[_SESSION_KEY] = state
    return state


def _build_default() -> SessionState:
    return SessionState(filters=_default_filters())


def reset_session() -> None:
    """Drop the cached SessionState so the next ``get_session()`` rebuilds it.

    Intended for tests and "clear state" buttons.
    """
    if st is None or not hasattr(st, "session_state"):
        return
    st.session_state.pop(_SESSION_KEY, None)


def as_dict(state: SessionState) -> dict[str, Any]:
    """Shallow dict for debugging / export."""
    out: dict[str, Any] = {}
    for f in fields(state):
        val = getattr(state, f.name)
        if hasattr(val, "__dict__"):
            out[f.name] = dict(val.__dict__)
        else:
            out[f.name] = val
    return out


__all__ = ["SessionState", "Filters", "get_session", "reset_session", "as_dict"]
