"""auth.settings — per-user preferences (timezone, theme, defaults, extras).

Design
------
Multi-user identity (``auth.users``), per-user data scoping (the
``user_id`` columns added in schema v7), audit logging (``auth.audit``),
and per-user API tokens (``auth.tokens``) already ship. The missing
piece is a place to persist preferences that AREN'T domain data —
things like "alice prefers America/New_York timezone" or "bob wants
the dashboard in dark mode".

This module provides that capability via:

* A new ``user_settings`` table (schema v15) holding one row per user,
  keyed by ``user_id``. The actual preferences live JSON-encoded in a
  single ``settings_json`` column, NOT in dedicated SQL columns. The
  one-column blob is deliberate — adding a new preference is a one-line
  change to the :class:`UserSettings` dataclass, with NO new migration
  required.
* :class:`UserSettings` — the dataclass shape callers consume. Carries
  the five documented fields (``timezone``, ``theme``,
  ``default_report_window_days``, ``default_alert_severity``, and a
  free-form ``extras`` dict for future per-user config).
* :func:`get_settings` — fetches the row, deserializes the JSON, returns
  a populated :class:`UserSettings`. Returns the defaults
  :class:`UserSettings` when no row exists (so callers do not have to
  handle the "user has never saved preferences" case). NEVER raises.
* :func:`save_settings` — INSERT-OR-REPLACEs the row, JSON-encoding the
  dataclass. Defensive: an invalid timezone is coerced to ``'UTC'``,
  an invalid theme to ``'auto'``. Audit-logs the save with the list of
  keys that changed (NOT the values themselves — a user's prefs are
  potentially sensitive). NEVER raises.
* :func:`update_setting` — fetch + update one key + save. Convenience
  for "change just the timezone" without rebuilding the whole dataclass.

What this module does NOT do
----------------------------
* No automatic seeding on signup. ``auth.users.signup`` does NOT call
  ``save_settings`` — a new user's row is absent until they explicitly
  save preferences (and ``get_settings`` returns the defaults in the
  interim). This avoids spending an INSERT per signup on data the user
  may never change.
* No secrets storage. Passwords, API tokens, vault contents — none of
  it belongs here. This module is for UI/UX knobs.
* No UI surface. Wiring this into ``ui.tab_setup`` (or wherever) is a
  follow-up commit.
* No cross-user reads. Settings are private — there is no
  ``list_all_settings`` admin helper.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger


# ─── Defaults ──────────────────────────────────────────────────────────────

# Defaults are module-level constants so callers (and tests) can compare
# against them without having to construct a fresh :class:`UserSettings`.
# Each one matches the "sensible neutral" that a brand-new user would
# expect on first login.
_DEFAULT_TIMEZONE: str = "UTC"
_DEFAULT_THEME: str = "auto"
_DEFAULT_REPORT_WINDOW_DAYS: int = 30
_DEFAULT_ALERT_SEVERITY: str = "LOW"


# ─── Validation sets ──────────────────────────────────────────────────────

# Themes are a closed set — adding a new one is a deliberate change to
# this module + the UI tab that consumes the value. Keep it small.
_VALID_THEMES: frozenset[str] = frozenset({"auto", "light", "dark"})

# Severity values come straight from the alert engine. Keep aligned with
# ``engine.alert_engine_v2``'s severity ladder so the UI threshold
# filter has the same vocabulary.
_VALID_ALERT_SEVERITIES: frozenset[str] = frozenset({"LOW", "MEDIUM", "HIGH", "CRITICAL"})


def _build_valid_timezones() -> frozenset[str]:
    """Resolve the timezone allowlist.

    Python 3.9+ ships ``zoneinfo`` in the stdlib; ``available_timezones()``
    returns the full IANA database (~600 entries on a typical install).
    On the off chance that fails (e.g. ``tzdata`` package missing on
    a slim Windows install), we fall back to a small hand-curated list
    covering the common US / EU / Asia / Pacific zones. Either way the
    set is non-empty so the ``timezone in _VALID_TIMEZONES`` check in
    :func:`save_settings` always behaves sanely.
    """
    try:
        import zoneinfo  # noqa: WPS433 (stdlib import, defensive only)

        zones = zoneinfo.available_timezones()
        # ``available_timezones()`` returns a ``set[str]`` — guard
        # against an empty result (shouldn't happen, but be defensive).
        if zones:
            return frozenset(zones)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"auth.settings: zoneinfo.available_timezones() failed, "
            f"using fallback allowlist: {exc}"
        )
    # Fallback: small hand-curated set covering common US / EU / Asia /
    # Pacific zones. NOT exhaustive — the zoneinfo path is the source
    # of truth for prod. This branch only fires when stdlib zoneinfo
    # is unavailable AND no tzdata package is installed.
    return frozenset({
        "UTC",
        "America/New_York", "America/Chicago", "America/Denver",
        "America/Los_Angeles", "America/Anchorage", "America/Honolulu",
        "America/Toronto", "America/Vancouver", "America/Sao_Paulo",
        "Europe/London", "Europe/Paris", "Europe/Berlin",
        "Europe/Madrid", "Europe/Rome", "Europe/Amsterdam",
        "Europe/Zurich", "Europe/Stockholm", "Europe/Moscow",
        "Asia/Tokyo", "Asia/Shanghai", "Asia/Hong_Kong",
        "Asia/Singapore", "Asia/Seoul", "Asia/Mumbai",
        "Asia/Dubai", "Asia/Jerusalem",
        "Australia/Sydney", "Australia/Melbourne",
        "Pacific/Auckland",
    })


_VALID_TIMEZONES: frozenset[str] = _build_valid_timezones()


# ─── Data ──────────────────────────────────────────────────────────────────

@dataclass
class UserSettings:
    """The per-user preferences blob.

    ``extras`` is the escape hatch for future per-user config — adding a
    new free-form key/value pair does NOT require a schema bump or even
    a code change here, just a UI tab that reads/writes it via
    :func:`update_setting`. Top-level fields are documented preferences
    with validation; ``extras`` is "anything else".

    Field-by-field defaults:

      * ``timezone`` — IANA name (e.g. ``"America/New_York"``). Defaults
        to ``"UTC"`` so UI elements that render timestamps have a sane
        zero state. Validated against ``_VALID_TIMEZONES`` at save time;
        an invalid value coerces to ``"UTC"`` (defensive — the UI is
        responsible for offering valid choices, but we never persist
        garbage).
      * ``theme`` — one of ``"auto"`` / ``"light"`` / ``"dark"``.
        ``"auto"`` follows the OS / browser preference and is the most
        forgiving default.
      * ``default_report_window_days`` — how many days of history the
        report tabs default to showing. 30 matches the existing tab
        defaults in ``ui/tab_report.py``.
      * ``default_alert_severity`` — the minimum severity threshold the
        alert UI defaults to surfacing. ``"LOW"`` shows everything;
        users can dial it up to ``"MEDIUM"`` / ``"HIGH"`` / ``"CRITICAL"``
        if they want a quieter dashboard.
      * ``extras`` — free-form ``dict[str, Any]``. Anything that
        serializes to JSON via ``json.dumps(default=str)`` round-trips.
    """
    user_id: str
    timezone: str = _DEFAULT_TIMEZONE
    theme: str = _DEFAULT_THEME
    default_report_window_days: int = _DEFAULT_REPORT_WINDOW_DAYS
    default_alert_severity: str = _DEFAULT_ALERT_SEVERITY
    extras: dict[str, Any] = field(default_factory=dict)


# ─── Internal helpers ──────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _defaults_for(user_id: str) -> UserSettings:
    """Build a fresh defaults dataclass stamped with the supplied user_id."""
    return UserSettings(user_id=user_id)


def _coerce_settings(settings: UserSettings) -> UserSettings:
    """Return a defensively-validated copy of ``settings``.

    The contract for :func:`save_settings` is that an invalid timezone
    or theme coerces to the default rather than raising. We do that here
    so the coercion happens in exactly one place — both ``save_settings``
    and any future code path that wants "what would this be saved as?"
    can call this and get the same answer.
    """
    tz = settings.timezone if settings.timezone in _VALID_TIMEZONES else _DEFAULT_TIMEZONE
    theme = settings.theme if settings.theme in _VALID_THEMES else _DEFAULT_THEME
    sev = (
        settings.default_alert_severity
        if settings.default_alert_severity in _VALID_ALERT_SEVERITIES
        else _DEFAULT_ALERT_SEVERITY
    )
    # report-window: must be a positive int. Coerce non-ints and
    # non-positives to the default — saves callers from passing
    # accidentally-zero or negative day counts.
    try:
        window = int(settings.default_report_window_days)
    except (TypeError, ValueError):
        window = _DEFAULT_REPORT_WINDOW_DAYS
    if window <= 0:
        window = _DEFAULT_REPORT_WINDOW_DAYS
    # extras: must be a dict (callers occasionally pass None or a list).
    extras = settings.extras if isinstance(settings.extras, dict) else {}
    return UserSettings(
        user_id=settings.user_id,
        timezone=tz,
        theme=theme,
        default_report_window_days=window,
        default_alert_severity=sev,
        extras=extras,
    )


def _serialize(settings: UserSettings) -> str:
    """Serialize a :class:`UserSettings` to the JSON blob shape stored
    in ``settings_json``.

    ``user_id`` is NOT included in the JSON — it lives in its own
    PRIMARY KEY column, and storing it twice would only invite
    consistency bugs.
    """
    payload = {
        "timezone": settings.timezone,
        "theme": settings.theme,
        "default_report_window_days": settings.default_report_window_days,
        "default_alert_severity": settings.default_alert_severity,
        "extras": settings.extras,
    }
    return json.dumps(payload, default=str)


def _deserialize(user_id: str, settings_json: str) -> UserSettings:
    """Map a stored JSON blob back into a :class:`UserSettings`.

    Malformed JSON or a payload missing some keys falls back to the
    defaults for each missing field — we NEVER refuse to return a
    populated dataclass for a known user. The function is paired with
    :func:`get_settings` which already handles the missing-row case.
    """
    try:
        parsed = json.loads(settings_json) if settings_json else {}
    except (TypeError, ValueError) as exc:
        logger.debug(
            f"auth.settings._deserialize: malformed JSON for user_id="
            f"{user_id!r}, falling back to defaults: {exc}"
        )
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    extras = parsed.get("extras", {})
    if not isinstance(extras, dict):
        extras = {}
    return UserSettings(
        user_id=user_id,
        timezone=str(parsed.get("timezone", _DEFAULT_TIMEZONE)),
        theme=str(parsed.get("theme", _DEFAULT_THEME)),
        default_report_window_days=int(
            parsed.get("default_report_window_days", _DEFAULT_REPORT_WINDOW_DAYS)
            or _DEFAULT_REPORT_WINDOW_DAYS
        ),
        default_alert_severity=str(
            parsed.get("default_alert_severity", _DEFAULT_ALERT_SEVERITY)
        ),
        extras=extras,
    )


def _changed_keys(prior: UserSettings, new: UserSettings) -> list[str]:
    """Return the list of top-level field names where ``new`` differs
    from ``prior``.

    The audit log records the LIST (not the values) so a security
    reviewer can see "alice changed timezone + theme on 5/22" without
    learning what she changed them TO. Each user's prefs are still
    potentially sensitive — knowing someone is in a particular timezone
    can deanonymize them across log sources.
    """
    keys: list[str] = []
    if prior.timezone != new.timezone:
        keys.append("timezone")
    if prior.theme != new.theme:
        keys.append("theme")
    if prior.default_report_window_days != new.default_report_window_days:
        keys.append("default_report_window_days")
    if prior.default_alert_severity != new.default_alert_severity:
        keys.append("default_alert_severity")
    # extras: compare dict equality. We treat ANY difference in extras
    # as a single "extras" change rather than enumerating sub-keys —
    # those keys are caller-defined and would leak whatever convention
    # the caller chose. The same security principle as the top-level
    # values: record the FACT of change, not the content.
    if prior.extras != new.extras:
        keys.append("extras")
    return keys


# ─── Public API ───────────────────────────────────────────────────────────

def get_settings(user_id: str) -> UserSettings:
    """Fetch the saved preferences for ``user_id``.

    Returns a fully-populated :class:`UserSettings` either way:

      * If a row exists, the stored JSON is deserialized and returned.
      * If no row exists (the user has never saved preferences), the
        defaults dataclass is returned with ``user_id`` set.

    NEVER raises — every failure path returns the defaults. The empty-
    string ``user_id`` (the "no user" legacy bucket) also returns the
    defaults, because there is no concept of "preferences for nobody".
    """
    try:
        if not isinstance(user_id, str):
            return _defaults_for("")

        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT settings_json FROM user_settings WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            return _defaults_for(user_id)
        return _deserialize(user_id, row["settings_json"] or "")
    except Exception as exc:  # noqa: BLE001 — generic catch by contract
        logger.warning(
            f"auth.settings.get_settings: failed for user_id={user_id!r}: {exc}"
        )
        return _defaults_for(user_id if isinstance(user_id, str) else "")


def save_settings(settings: UserSettings) -> bool:
    """Persist a :class:`UserSettings` to the database.

    Returns ``True`` on success, ``False`` on any validation or
    persistence failure. NEVER raises. The function:

      1. Validates ``settings`` is a :class:`UserSettings` with a
         non-empty ``user_id``. A bad input returns ``False`` without
         touching the DB.
      2. Coerces invalid values to their defaults (invalid timezone
         → ``"UTC"``, invalid theme → ``"auto"``, non-positive report
         window → 30, invalid severity → ``"LOW"``, non-dict extras
         → ``{}``). This is the defensive layer between the UI's
         freeform inputs and the persistence layer.
      3. Fetches the prior row (if any) so the audit hook can record
         WHICH keys changed.
      4. INSERTs-OR-REPLACEs the row.
      5. Records an audit event whose ``detail`` payload carries the
         changed-keys LIST (not the values themselves — see
         ``_changed_keys``).
    """
    try:
        if not isinstance(settings, UserSettings):
            logger.warning(
                f"auth.settings.save_settings: input is not a UserSettings "
                f"(got {type(settings).__name__})"
            )
            return False
        if not isinstance(settings.user_id, str) or not settings.user_id:
            logger.warning(
                "auth.settings.save_settings: missing user_id, refusing to save"
            )
            return False

        # Coerce BEFORE diffing so the audit log reflects what was
        # actually written (a UI that POSTs an invalid timezone still
        # ends up persisting UTC; the audit log should show "timezone
        # changed" only if UTC differs from what was there before).
        coerced = _coerce_settings(settings)
        prior = get_settings(settings.user_id)
        keys_changed = _changed_keys(prior, coerced)

        payload = _serialize(coerced)
        now = _now_iso()

        from state.db import get_connection

        conn = get_connection()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO user_settings
                  (user_id, settings_json, updated_at)
                VALUES (?, ?, ?)
                """,
                (coerced.user_id, payload, now),
            )

        # Audit-log the save. Best-effort — a failed audit-write must
        # NOT fail the save (the user's prefs are already in the DB
        # at this point).
        try:
            from auth.audit import record_audit
            record_audit(
                "save_user_settings",
                entity_type="user_settings",
                entity_id=coerced.user_id,
                detail={"keys_changed": keys_changed},
                user_id=coerced.user_id,
            )
        except Exception:  # noqa: BLE001
            pass
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"auth.settings.save_settings: failed for "
            f"user_id={getattr(settings, 'user_id', None)!r}: {exc}"
        )
        return False


def update_setting(user_id: str, key: str, value: Any) -> bool:
    """Convenience helper: change ONE preference key for ``user_id``.

    Fetches the current settings (or the defaults if none saved),
    overwrites ``key`` with ``value``, and saves. Returns ``True`` on
    success, ``False`` on any failure. NEVER raises.

    Recognized top-level keys: ``timezone``, ``theme``,
    ``default_report_window_days``, ``default_alert_severity``. Any
    other ``key`` goes into the ``extras`` dict (so this helper is also
    the way to set per-user free-form config without rebuilding the
    whole dataclass).

    Note: the defensive coercion in :func:`save_settings` still applies
    — passing ``timezone="Mars/Olympus"`` will silently coerce to UTC.
    Callers that want strict validation should check the key against
    the public allowlists themselves before calling this.
    """
    try:
        if not isinstance(user_id, str) or not user_id:
            return False
        if not isinstance(key, str) or not key:
            return False

        current = get_settings(user_id)
        if key == "timezone":
            current.timezone = str(value)
        elif key == "theme":
            current.theme = str(value)
        elif key == "default_report_window_days":
            try:
                current.default_report_window_days = int(value)
            except (TypeError, ValueError):
                # Invalid — let save_settings's coercion handle it.
                current.default_report_window_days = _DEFAULT_REPORT_WINDOW_DAYS
        elif key == "default_alert_severity":
            current.default_alert_severity = str(value)
        else:
            # Anything else falls into extras. This is the
            # zero-schema-bump extension point — adding a new
            # preference is purely a UI concern.
            current.extras[key] = value
        return save_settings(current)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"auth.settings.update_setting: failed for "
            f"user_id={user_id!r}, key={key!r}: {exc}"
        )
        return False


__all__ = [
    "UserSettings",
    "get_settings",
    "save_settings",
    "update_setting",
]
