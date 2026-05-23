"""utils.tz — per-user timezone formatting helpers.

The persistence layer stores every timestamp in UTC (ISO 8601 strings,
``datetime.now(timezone.utc)``, etc.). The UI, however, should render
those timestamps in the *viewer's* timezone — an authenticated user
whose ``auth.settings.UserSettings.timezone`` is ``"America/New_York"``
expects to see ``2026-05-22 09:15 EDT`` rather than the raw
``2026-05-22 13:15 UTC``.

This module bridges those two worlds. Four tiny helpers cover every
flow the UI tabs need:

* :func:`get_user_timezone` — fetch the active user's IANA timezone
  string from ``auth.settings``, falling back to ``"UTC"`` on any
  failure (no user, settings module unimportable, DB down, whatever).
* :func:`to_user_tz` — accept a UTC ``datetime`` OR an ISO-8601 string
  and return a timezone-aware ``datetime`` in the user's TZ. Bad input
  (unparseable string, unknown zone, missing zoneinfo data) returns the
  input unchanged so callers never have to wrap individual conversions
  in try/except.
* :func:`format_user_tz` — strftime in the user's TZ, returning the
  empty string on any failure. The single call most UI tabs need.
* :func:`now_in_user_tz` — current time in the user's TZ. Convenience
  for "last refreshed at HH:MM" footers.

Contract: EVERY public function in this module NEVER raises. The UI
tab files import these freely; a bad timezone string or a missing
``zoneinfo`` package must NEVER take a tab down. Failure paths
collapse to safe defaults (UTC for the tz lookup, the input unchanged
for the conversion, the empty string for the formatter).

The module deliberately does NOT cache anything. ``get_settings`` is
cheap (one indexed SQLite read), the ``zoneinfo.ZoneInfo`` constructor
internally caches by zone name, and a per-call lookup means the user's
timezone change in the UI takes effect on the very next render.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Union

from loguru import logger


# ─── Internal helpers ──────────────────────────────────────────────────────

def _resolve_user_id(user_id: Optional[str]) -> str:
    """Return ``user_id`` if non-None, else look it up via
    ``state.user_scope.current_user_id``.

    Defensive: a failure inside ``current_user_id`` (Streamlit not
    importable, session_state missing, …) returns the empty string. The
    empty string is the "no user" sentinel — :func:`get_user_timezone`
    returns ``"UTC"`` for it.
    """
    if user_id is not None:
        return user_id if isinstance(user_id, str) else ""
    try:
        from state.user_scope import current_user_id
        return current_user_id() or ""
    except Exception:
        return ""


def _coerce_to_utc_datetime(
    dt_or_iso: Union[datetime, str],
) -> Optional[datetime]:
    """Parse the input into a timezone-aware UTC datetime.

    Returns:
      * a UTC ``datetime`` on success;
      * ``None`` if the input cannot be parsed (caller's signal to fall
        back to the raw input).

    Accepts:
      * A timezone-aware ``datetime`` (returned as-is, converted to UTC).
      * A naive ``datetime`` (assumed to be UTC — matches the
        persistence contract).
      * An ISO 8601 string parseable by ``datetime.fromisoformat``.
        Trailing ``"Z"`` is normalized to ``"+00:00"`` because
        ``fromisoformat`` rejects the ``Z`` suffix on Python 3.9
        (3.11 added support but we run on 3.9).
    """
    try:
        if isinstance(dt_or_iso, datetime):
            dt = dt_or_iso
        elif isinstance(dt_or_iso, str):
            s = dt_or_iso.strip()
            if not s:
                return None
            # fromisoformat on 3.9 does not accept the 'Z' suffix.
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
        else:
            return None
    except (TypeError, ValueError):
        return None
    # Treat a naive datetime as UTC (matches persistence-layer
    # convention; UI never persists localized timestamps).
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


# ─── Public API ───────────────────────────────────────────────────────────

def get_user_timezone(*, user_id: Optional[str] = None) -> str:
    """Return the active user's IANA timezone string.

    Resolves ``user_id`` via :func:`state.user_scope.current_user_id`
    when not supplied, then asks :func:`auth.settings.get_settings` for
    the saved timezone. Returns ``"UTC"`` on ANY failure path — empty
    user id, settings module unimportable, DB error, missing
    ``timezone`` attribute, whatever. NEVER raises.

    The empty-string user id (the "no user" sentinel) maps to ``"UTC"``
    deliberately — there is no concept of "preferences for nobody", so
    the briefing tab simply renders in UTC for un-authenticated views.
    """
    try:
        uid = _resolve_user_id(user_id)
        if not uid:
            return "UTC"
        from auth.settings import get_settings
        settings = get_settings(uid)
        tz_name = getattr(settings, "timezone", "UTC")
        if not isinstance(tz_name, str) or not tz_name:
            return "UTC"
        return tz_name
    except Exception as exc:  # noqa: BLE001 — contract: never raises
        logger.debug(f"utils.tz.get_user_timezone: falling back to UTC: {exc}")
        return "UTC"


def to_user_tz(
    dt_or_iso: Union[datetime, str],
    *,
    user_id: Optional[str] = None,
) -> Union[datetime, str]:
    """Convert a UTC datetime (or ISO-8601 string) into the user's TZ.

    Returns a timezone-aware ``datetime`` on success. On ANY failure
    (unparseable string, unknown zone, missing zoneinfo data, …)
    returns the input unchanged — callers can use this output directly
    in an f-string without wrapping the call in try/except.

    The convert-or-pass-through semantics keep the briefing tab's
    formatting code linear: a malformed ISO string still renders (as
    itself) rather than blanking the field.
    """
    parsed = _coerce_to_utc_datetime(dt_or_iso)
    if parsed is None:
        return dt_or_iso
    try:
        import zoneinfo
        tz_name = get_user_timezone(user_id=user_id)
        try:
            tz = zoneinfo.ZoneInfo(tz_name)
        except Exception:
            # Unknown zone name (corrupted settings, tzdata missing
            # for that entry) — return the UTC datetime unchanged.
            return parsed
        return parsed.astimezone(tz)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"utils.tz.to_user_tz: falling back to input: {exc}")
        return dt_or_iso


def format_user_tz(
    dt_or_iso: Union[datetime, str],
    fmt: str = "%Y-%m-%d %H:%M %Z",
    *,
    user_id: Optional[str] = None,
) -> str:
    """Format a UTC datetime (or ISO-8601 string) in the user's TZ.

    Returns the strftime output in the user's timezone. Returns the
    empty string on ANY failure — unparseable input, unknown zone,
    a strftime directive the platform rejects, etc. Callers can drop
    the result directly into a UI string without an existence check
    (the empty string renders harmlessly).

    The default ``fmt`` includes ``%Z`` so the rendered zone
    abbreviation (EDT, JST, UTC, …) is unambiguous — a viewer can
    always tell at a glance what timezone the timestamp is in.
    """
    try:
        converted = to_user_tz(dt_or_iso, user_id=user_id)
        if not isinstance(converted, datetime):
            # to_user_tz returned the raw string — convert path failed.
            # We refuse to format an unparseable input.
            return ""
        return converted.strftime(fmt)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"utils.tz.format_user_tz: failed, returning empty: {exc}")
        return ""


def now_in_user_tz(*, user_id: Optional[str] = None) -> datetime:
    """Return ``datetime.now`` in the user's timezone.

    On ANY failure returns ``datetime.now(timezone.utc)`` — the caller
    always gets a usable timezone-aware datetime back.
    """
    try:
        now_utc = datetime.now(timezone.utc)
        converted = to_user_tz(now_utc, user_id=user_id)
        # to_user_tz returns the input unchanged on failure; for a
        # genuine UTC datetime input we always get a datetime back.
        if isinstance(converted, datetime):
            return converted
        return now_utc
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"utils.tz.now_in_user_tz: falling back to UTC now: {exc}")
        return datetime.now(timezone.utc)


__all__ = [
    "get_user_timezone",
    "to_user_tz",
    "format_user_tz",
    "now_in_user_tz",
]
