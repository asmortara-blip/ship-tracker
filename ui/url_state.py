"""ui/url_state.py — bookmarkable / shareable section routing via the URL query.

Pure helpers that map between the ``?section=`` query parameter and the app's
active section, so a view survives a refresh + the browser back-button and can
be shared by URL. Streamlit's ``st.query_params`` is read/written in ``app.py``;
all the *logic* (validation against the known sections, defaulting, unwrapping
list-valued params) lives here so it is unit-testable without a Streamlit
runtime.

Scope note — SECTION-level routing only. Streamlit's inner ``st.tabs()``
selection is client-side and cannot be set programmatically, so the tab a user
picks *within* a section is not (yet) URL-addressable; routing lands on the
section, matching the existing pinned-tabs behaviour.
"""
from __future__ import annotations

from typing import Iterable, Mapping, Optional

# The query-string key that carries the active section, e.g. ``?section=markets``.
SECTION_QUERY_KEY = "section"


def _coerce_scalar(raw: object) -> Optional[str]:
    """Unwrap a query-param value to a single trimmed string.

    Streamlit normally returns a scalar string, but a repeated param
    (``?section=a&section=b``) can surface as a list/tuple — take the first.
    Returns ``None`` for missing/empty values.
    """
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else None
        if raw is None:
            return None
    text = str(raw).strip()
    return text or None


def resolve_active_section(
    query_params: Mapping[str, object] | None,
    valid_keys: Iterable[str],
    *,
    default: str = "dashboard",
) -> str:
    """Pick the section to show on a fresh load.

    Returns the ``?section=`` value when it is one of ``valid_keys``; otherwise
    ``default`` (or, if ``default`` itself isn't valid, the first valid key).
    Robust to a missing/empty mapping, a list-valued param, and unknown keys —
    never raises.
    """
    valid = set(valid_keys)
    fallback = default if default in valid else next(iter(sorted(valid)), default)
    if not query_params:
        return fallback
    key = _coerce_scalar(query_params.get(SECTION_QUERY_KEY))
    if key is None:
        return fallback
    return key if key in valid else fallback


def section_url_query(section: str) -> dict[str, str]:
    """The query-param dict that addresses ``section`` (for building share links)."""
    return {SECTION_QUERY_KEY: str(section)}
