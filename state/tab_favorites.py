"""state.tab_favorites — per-user pinned-tab helpers.

The Ship Tracker exposes ~60+ tabs across 10 sidebar sections. Different
operators each gravitate to a different subset — an alerts-and-risk
person lives in ``tab_alerts`` / ``tab_risk_lab``, a portfolio operator
lives in ``tab_portfolio`` / ``tab_alpha`` / ``tab_backtest``, etc. This
module lets every user pin their favourite tabs so the most-used surfaces
are one click away from any section.

Design
------
Storage piggybacks on the existing :class:`auth.settings.UserSettings`
``extras`` dict — that field is the documented zero-schema-bump
extension point for per-user free-form config. The pinned list lives at
``extras['pinned_tabs']`` as a JSON-encoded list of module-name strings
(e.g. ``['ui.tab_alerts', 'ui.tab_portfolio']``). Order is the user's
preferred display order; we never re-sort it.

Every helper:

* Resolves ``user_id`` via :func:`state.user_scope.current_user_id` when
  the caller doesn't pass one explicitly. The empty string (the legacy
  "no user" bucket / logged-out user) means "nothing to pin" — every
  helper short-circuits to a sensible no-op rather than mutating shared
  state.
* NEVER raises. A bad input, a corrupted extras blob, a DB failure —
  every failure path returns an empty list / ``False`` / ``True`` (for
  idempotent no-ops). The sidebar must NEVER crash because a user has
  garbage in their settings.
* Treats the pinned list as opaque strings. We do not validate that a
  pinned ``tab_module`` actually exists in the codebase — that's the
  caller's responsibility (the UI can surface a "this pin is dead" hint
  if it wants). This keeps the storage layer decoupled from the live
  tab inventory.

What this module does NOT do
----------------------------
* No section-aware logic. The module name is the unit of pinning. The
  UI knows which section a pinned module belongs to and routes there.
* No default pins on signup. Pinning is strictly opt-in.
* No schema bump. The whole feature lives inside the existing
  ``user_settings.settings_json`` blob.
"""
from __future__ import annotations

from typing import Optional

from loguru import logger


# Key inside ``UserSettings.extras`` where the pinned list lives.
# Centralized so the storage key has exactly one source of truth.
_EXTRAS_KEY: str = "pinned_tabs"


def _resolve_user_id(user_id: Optional[str]) -> str:
    """Return ``user_id`` or fall back to the active Streamlit user.

    Returns the empty string when neither source yields a non-empty
    string — every public helper treats ``""`` as "nothing to do".
    """
    if isinstance(user_id, str) and user_id:
        return user_id
    try:
        from state.user_scope import current_user_id
        resolved = current_user_id()
        return resolved if isinstance(resolved, str) else ""
    except Exception:  # noqa: BLE001 — never let resolution take down callers
        return ""


def _read_pinned_raw(uid: str) -> list[str]:
    """Fetch the raw pinned-list from the user's extras blob.

    Defensive: a corrupted blob (non-list at the key, non-string entries,
    etc.) silently degrades to an empty list. We NEVER surface a parse
    error to the sidebar.
    """
    try:
        from auth.settings import get_settings
        settings = get_settings(uid)
        extras = getattr(settings, "extras", None)
        if not isinstance(extras, dict):
            return []
        raw = extras.get(_EXTRAS_KEY)
        if not isinstance(raw, list):
            return []
        # Filter to non-empty strings only — drops Nones, ints, dicts,
        # whatever else may have crept in via a malformed import. Order
        # is preserved.
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, str) or not item:
                continue
            if item in seen:
                # De-dupe defensively — pinning is idempotent at write
                # time but a hand-edited DB row might still contain
                # duplicates.
                continue
            seen.add(item)
            cleaned.append(item)
        return cleaned
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"state.tab_favorites._read_pinned_raw: {exc}")
        return []


def _write_pinned(uid: str, modules: list[str]) -> bool:
    """Persist ``modules`` as the user's pinned-list.

    Returns ``True`` on success, ``False`` on failure. Routes through
    :func:`auth.settings.update_setting` (which round-trips through
    ``save_settings``'s defensive coercion + audit hook).
    """
    try:
        from auth.settings import update_setting
        return bool(update_setting(uid, _EXTRAS_KEY, list(modules)))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"state.tab_favorites._write_pinned: failed for "
            f"user_id={uid!r}: {exc}"
        )
        return False


# ─── Public API ───────────────────────────────────────────────────────────


def get_pinned_tabs(*, user_id: Optional[str] = None) -> list[str]:
    """Return the user's pinned-tab module names in display order.

    The returned list is a fresh copy — mutating it does NOT touch the
    stored value. An unknown user, a logged-out caller, a corrupted
    extras blob, or any other failure returns ``[]`` (NEVER raises).
    """
    uid = _resolve_user_id(user_id)
    if not uid:
        return []
    return _read_pinned_raw(uid)


def pin_tab(tab_module: str, *, user_id: Optional[str] = None) -> bool:
    """Pin ``tab_module`` for the user, appending to the end of the list.

    Idempotent: pinning an already-pinned module is a no-op that returns
    ``True`` (the post-condition "this module is pinned" is satisfied).

    Returns ``False`` only when:
      * ``tab_module`` is not a non-empty string, OR
      * The active user cannot be resolved (logged-out caller), OR
      * The underlying save fails.

    NEVER raises.
    """
    try:
        if not isinstance(tab_module, str) or not tab_module:
            return False
        uid = _resolve_user_id(user_id)
        if not uid:
            return False
        current = _read_pinned_raw(uid)
        if tab_module in current:
            return True  # already pinned — idempotent success
        return _write_pinned(uid, current + [tab_module])
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"state.tab_favorites.pin_tab: failed for "
            f"tab_module={tab_module!r}: {exc}"
        )
        return False


def unpin_tab(tab_module: str, *, user_id: Optional[str] = None) -> bool:
    """Unpin ``tab_module`` for the user.

    Idempotent: unpinning an already-unpinned module is a no-op that
    returns ``True`` (the post-condition "this module is not pinned" is
    satisfied). Returns ``False`` only on bad input or save failure.

    NEVER raises.
    """
    try:
        if not isinstance(tab_module, str) or not tab_module:
            return False
        uid = _resolve_user_id(user_id)
        if not uid:
            return False
        current = _read_pinned_raw(uid)
        if tab_module not in current:
            return True  # already unpinned — idempotent success
        return _write_pinned(uid, [m for m in current if m != tab_module])
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"state.tab_favorites.unpin_tab: failed for "
            f"tab_module={tab_module!r}: {exc}"
        )
        return False


def reorder_pinned_tabs(
    tab_modules: list[str], *, user_id: Optional[str] = None
) -> bool:
    """Replace the full pinned-list with ``tab_modules`` in the supplied order.

    Strict membership check: every entry in ``tab_modules`` MUST already
    be in the user's current pinned-list. If ANY entry is not pinned,
    the call is rejected wholesale (returns ``False``, no writes). This
    keeps the helper purely a re-ordering operation — adding or removing
    pins goes through :func:`pin_tab` / :func:`unpin_tab`.

    Also rejects:

      * Non-list ``tab_modules`` (returns ``False``).
      * Lists with non-string entries (returns ``False``).
      * Duplicate entries (returns ``False``).
      * Lists whose length differs from the current pinned-list length
        (returns ``False``) — a partial reorder is ambiguous.

    NEVER raises.
    """
    try:
        if not isinstance(tab_modules, list):
            return False
        # Reject non-string / empty-string entries.
        for item in tab_modules:
            if not isinstance(item, str) or not item:
                return False
        # Reject duplicates.
        if len(set(tab_modules)) != len(tab_modules):
            return False
        uid = _resolve_user_id(user_id)
        if not uid:
            return False
        current = _read_pinned_raw(uid)
        # Length must match — a reorder is a permutation, not an add/remove.
        if len(tab_modules) != len(current):
            return False
        # Membership: every supplied entry must be in the current pinned list.
        current_set = set(current)
        for item in tab_modules:
            if item not in current_set:
                return False
        return _write_pinned(uid, list(tab_modules))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"state.tab_favorites.reorder_pinned_tabs: failed: {exc}"
        )
        return False


def is_pinned(tab_module: str, *, user_id: Optional[str] = None) -> bool:
    """Convenience query: is ``tab_module`` currently pinned for the user?

    Returns ``False`` for any failure path (bad input, logged-out user,
    corrupted blob). NEVER raises. Mirrors the same defensive contract
    as the mutating helpers.
    """
    try:
        if not isinstance(tab_module, str) or not tab_module:
            return False
        uid = _resolve_user_id(user_id)
        if not uid:
            return False
        return tab_module in _read_pinned_raw(uid)
    except Exception:  # noqa: BLE001
        return False


__all__ = [
    "get_pinned_tabs",
    "pin_tab",
    "unpin_tab",
    "reorder_pinned_tabs",
    "is_pinned",
]
