"""auth.notification_prefs — per-user notification routing preferences.

Design
------
Today an alert reaches a delivery channel iff:

  1. The rule that fired the alert listed the channel by name (or had an
     empty list, meaning "broadcast to all"), AND
  2. The channel's per-channel ``severity_threshold`` is at or below the
     alert's severity, AND
  3. The channel is not in its own quiet-hours window.

That is RULE-shaped and CHANNEL-shaped routing — the operator who
authored the rule and the operator who authored the channel decide
together. This module adds a third, USER-shaped filter layer on top:
the operator who will RECEIVE the notification gets to express their
own preferences ("only HIGH+ at night", "send CRITICAL to my pager",
"don't bother me with MACRO alerts").

The prefs are evaluated AFTER the existing rule + channel gates by
``engine.alert_delivery.deliver_alert``. The existing filters are NOT
modified — prefs are a SUBTRACTIVE last-mile filter that can only
remove channels, never add them.

Storage
-------
One JSON blob per user in the ``kv_state`` table keyed by
``notification_prefs:{user_id}``. No schema bump — the kv_state row
is the single-blob-per-user pattern shared with
``state.user_filters`` / ``auth.settings``. A user without a row
returns the defaults (everything enabled, no filters).

Severity ranking
----------------
The brief uses a HIGH-NUMBER-IS-WORSE convention (CRITICAL=4 / HIGH=3
/ MEDIUM=2 / LOW=1) for the prefs floor — that's the natural ">=" form
that an operator expects when they say ``min_severity=HIGH``. This is
the INVERSE of ``alert_engine_v2._SEVERITY_ORDER`` which uses
HIGH-NUMBER-IS-LESS-SEVERE for sorting (CRITICAL=0). The two
conventions coexist intentionally — sorters want CRITICAL first,
floor-checks want CRITICAL highest. The local map keeps the
``_meets_min_severity`` arithmetic readable.

Quiet hours
-----------
The user-level quiet-hours window is a (start_hour_utc, end_hour_utc)
tuple of integers. CRITICAL alerts ALWAYS pass through — incident
response is never quieted by a user pref. The window can wrap
midnight (e.g. ``(22, 6)`` means 22:00 → 06:00 the next day) and the
gate treats the window as ``start <= hour < end`` for normal windows
and ``hour >= start OR hour < end`` for wrap-around windows.

What this module does NOT do
----------------------------
* Does NOT modify the per-rule routing (``filter_channels_by_rule``).
* Does NOT modify the per-channel severity_threshold gate or the
  per-channel quiet-hours window.
* Does NOT introduce a new channel kind.
* Does NOT bump SCHEMA_VERSION.
* Does NOT cross user boundaries — alice's prefs cannot affect bob's
  delivery.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger


# ─── Storage key + defaults ───────────────────────────────────────────────

_KEY_PREFIX: str = "notification_prefs:"
_DEFAULT_MIN_SEVERITY: str = "LOW"

# HIGH-NUMBER-IS-WORSE — the natural form for a "min_severity floor"
# pref. Inverse of ``alert_engine_v2._SEVERITY_ORDER`` (which uses
# HIGH-NUMBER-IS-LESS-SEVERE because it doubles as a sort key).
_SEVERITY_RANK: dict[str, int] = {
    "CRITICAL": 4,
    "HIGH":     3,
    "MEDIUM":   2,
    "LOW":      1,
}

_VALID_SEVERITIES: tuple[str, ...] = ("CRITICAL", "HIGH", "MEDIUM", "LOW")

# Counter for prefs-suppressed channels. Lives in kv_state alongside
# ``alerts_suppressed_by_cooldown`` (engine.alert_engine_v2). The
# counter is per-channel-drop, not per-alert — a single alert with
# three channels dropped to zero increments by three. Cheap operator
# telemetry; not authoritative.
_PREFS_SUPPRESSED_KEY: str = "alerts_suppressed_by_prefs"


# ─── Data ──────────────────────────────────────────────────────────────────

@dataclass
class NotificationPrefs:
    """Per-user notification preferences.

    Fields:
      * ``user_id``                — the user this preference set belongs
        to. The id is also encoded in the kv_state key, so it is
        effectively a primary key — different ``user_id`` values land
        in different rows.
      * ``enabled``                — master switch. ``False`` short-
        circuits the filter to ``[]`` (no channels). Default ``True``.
      * ``min_severity``           — the floor. Alerts whose severity
        rank is below the floor are suppressed. Default ``"LOW"``
        (no suppression). Valid values: CRITICAL / HIGH / MEDIUM / LOW.
      * ``severity_channel_map``   — per-severity allow-list. When the
        map has an entry for ``alert.severity``, ONLY channels whose
        ``channel_id`` is in the list pass. When the map has no entry
        for that severity, no per-severity restriction is applied.
        Default ``{}`` (no restrictions).
      * ``alert_type_filter``      — global allow-list of alert_types.
        When non-empty, an alert whose ``alert_type`` is not in the
        list is suppressed entirely. When empty (default), no filter
        is applied.
      * ``quiet_during_hours``     — optional ``(start_hour, end_hour)``
        UTC tuple. When the current UTC hour falls inside the window,
        non-CRITICAL alerts are suppressed; CRITICAL always passes.
        ``None`` (default) means no quiet hours.
    """
    user_id: str
    enabled: bool = True
    min_severity: str = _DEFAULT_MIN_SEVERITY
    severity_channel_map: dict[str, list[str]] = field(default_factory=dict)
    alert_type_filter: list[str] = field(default_factory=list)
    quiet_during_hours: Optional[tuple[int, int]] = None


# ─── Internal helpers ──────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_key(user_id: str) -> str:
    return f"{_KEY_PREFIX}{user_id}"


def _coerce_user_id(user_id: Any) -> str:
    """Coerce ``user_id`` to a non-None string. Tests can pass an empty
    string to scope to the legacy bucket; arbitrary types coerce to
    str() to avoid raising on a bad caller."""
    if user_id is None:
        return ""
    if isinstance(user_id, str):
        return user_id
    try:
        return str(user_id)
    except Exception:
        return ""


def _normalize_severity(value: Any, fallback: str = _DEFAULT_MIN_SEVERITY) -> str:
    """Coerce ``value`` into one of the canonical severity strings.
    Anything outside the four valid values falls back to ``fallback``.
    Case-insensitive."""
    if isinstance(value, str):
        upper = value.strip().upper()
        if upper in _SEVERITY_RANK:
            return upper
    return fallback


def _normalize_severity_channel_map(value: Any) -> dict[str, list[str]]:
    """Coerce the persisted/incoming map to ``{SEVERITY: [channel_id, ...]}``.

    A non-dict input returns ``{}``. Keys are upper-cased and dropped
    if not a known severity. Values that are not lists or that contain
    non-string entries are filtered defensively — the whole point of
    this helper is that a corrupted blob from disk MUST NOT raise
    into the filter helper.
    """
    if not isinstance(value, dict):
        return {}
    out: dict[str, list[str]] = {}
    for sev, channels in value.items():
        if not isinstance(sev, str):
            continue
        sev_upper = sev.strip().upper()
        if sev_upper not in _SEVERITY_RANK:
            continue
        if not isinstance(channels, list):
            continue
        cleaned = [c for c in channels if isinstance(c, str)]
        out[sev_upper] = cleaned
    return out


def _normalize_alert_type_filter(value: Any) -> list[str]:
    """Coerce a persisted/incoming alert_type list to ``list[str]``.

    A non-list input returns ``[]`` (the "no filter" default). Non-
    string entries are dropped silently; we never raise on garbage.
    """
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, str) and v]


def _normalize_quiet_hours(value: Any) -> Optional[tuple[int, int]]:
    """Coerce a persisted/incoming quiet-hours value to ``(int, int)`` or
    ``None``.

    Accepts a 2-element list/tuple of integers in [0, 23]. Anything
    else (including ``None``, missing fields, out-of-range, malformed)
    returns ``None`` — "no quiet hours configured".
    """
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        start = int(value[0])
        end = int(value[1])
    except (TypeError, ValueError):
        return None
    if not (0 <= start <= 23 and 0 <= end <= 23):
        return None
    return (start, end)


def _load_blob(user_id: str) -> dict[str, Any]:
    """Read the user's prefs blob from kv_state.

    Returns an empty dict when the row is missing or when the JSON
    fails to parse. NEVER raises — callers expect a dict.
    """
    try:
        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM kv_state WHERE key = ?", (_row_key(user_id),)
        ).fetchone()
        if row is None:
            return {}
        raw = row["value"] if hasattr(row, "keys") else row[0]
        if not raw:
            return {}
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return {}
        return parsed
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"auth.notification_prefs._load_blob: read failed for "
            f"user_id={user_id!r}: {exc}"
        )
        return {}


def _save_blob(user_id: str, blob: dict[str, Any]) -> bool:
    """Persist the user's prefs blob to kv_state. Returns success."""
    try:
        from state.db import get_connection

        payload = json.dumps(blob, default=str)
        now = _now_iso()
        conn = get_connection()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_state (key, value, updated_at) "
                "VALUES (?, ?, ?)",
                (_row_key(user_id), payload, now),
            )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"auth.notification_prefs._save_blob: write failed for "
            f"user_id={user_id!r}: {exc}"
        )
        return False


def _blob_to_prefs(user_id: str, blob: dict[str, Any]) -> NotificationPrefs:
    """Reconstruct a :class:`NotificationPrefs` from the raw JSON dict.

    Every field is run through its normalize_* helper so a partially-
    corrupted blob still yields a working dataclass — the worst case
    is "fields fall back to defaults", never an exception.
    """
    enabled_raw = blob.get("enabled", True)
    enabled = bool(enabled_raw) if isinstance(enabled_raw, (bool, int)) else True
    return NotificationPrefs(
        user_id=user_id,
        enabled=enabled,
        min_severity=_normalize_severity(blob.get("min_severity")),
        severity_channel_map=_normalize_severity_channel_map(
            blob.get("severity_channel_map")
        ),
        alert_type_filter=_normalize_alert_type_filter(
            blob.get("alert_type_filter")
        ),
        quiet_during_hours=_normalize_quiet_hours(
            blob.get("quiet_during_hours")
        ),
    )


def _prefs_to_blob(prefs: NotificationPrefs) -> dict[str, Any]:
    """Serialize a :class:`NotificationPrefs` to the dict shape stored
    in kv_state. ``user_id`` is dropped — it's encoded in the row key
    and storing it twice invites drift between the key and the body.
    """
    return {
        "enabled":              bool(prefs.enabled),
        "min_severity":         _normalize_severity(prefs.min_severity),
        "severity_channel_map": _normalize_severity_channel_map(
            prefs.severity_channel_map
        ),
        "alert_type_filter":    _normalize_alert_type_filter(
            prefs.alert_type_filter
        ),
        "quiet_during_hours":   list(prefs.quiet_during_hours)
                                if prefs.quiet_during_hours is not None
                                else None,
    }


# ─── Public API ───────────────────────────────────────────────────────────

def get_prefs(*, user_id: str) -> NotificationPrefs:
    """Return the user's :class:`NotificationPrefs`.

    Returns the defaults (enabled, no filters) when the user has never
    saved prefs OR when the kv_state row is corrupted. NEVER raises.
    """
    uid = _coerce_user_id(user_id)
    blob = _load_blob(uid)
    return _blob_to_prefs(uid, blob)


def save_prefs(prefs: NotificationPrefs, *, user_id: Optional[str] = None) -> bool:
    """Persist ``prefs`` to the user's row.

    Args:
        prefs:    The :class:`NotificationPrefs` to save. A non-prefs
                  input returns ``False`` without touching the DB.
        user_id:  Override the user id encoded in ``prefs.user_id``.
                  When ``None`` (default) the dataclass's own
                  ``user_id`` is used.

    Returns:
        ``True`` on success, ``False`` on any validation or persistence
        failure. NEVER raises.
    """
    try:
        if not isinstance(prefs, NotificationPrefs):
            return False
        uid = _coerce_user_id(user_id if user_id is not None else prefs.user_id)
        blob = _prefs_to_blob(prefs)
        return _save_blob(uid, blob)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"auth.notification_prefs.save_prefs: failed: {exc}"
        )
        return False


def update_pref(*, user_id: str, **kwargs: Any) -> bool:
    """Partial update — apply only the fields supplied in ``kwargs``.

    Behaviour:
      * Reads the current prefs (defaults if no row).
      * For each kwarg whose key matches a :class:`NotificationPrefs`
        field, replaces the value (after normalization).
      * Unknown kwargs are silently ignored — the helper is forward-
        compatible with callers that pass extra fields.
      * Writes the merged record back.

    Returns ``True`` on success, ``False`` on persistence failure.
    NEVER raises.
    """
    try:
        uid = _coerce_user_id(user_id)
        current = get_prefs(user_id=uid)
        if "enabled" in kwargs:
            current.enabled = bool(kwargs["enabled"])
        if "min_severity" in kwargs:
            current.min_severity = _normalize_severity(
                kwargs["min_severity"], fallback=current.min_severity,
            )
        if "severity_channel_map" in kwargs:
            current.severity_channel_map = _normalize_severity_channel_map(
                kwargs["severity_channel_map"]
            )
        if "alert_type_filter" in kwargs:
            current.alert_type_filter = _normalize_alert_type_filter(
                kwargs["alert_type_filter"]
            )
        if "quiet_during_hours" in kwargs:
            current.quiet_during_hours = _normalize_quiet_hours(
                kwargs["quiet_during_hours"]
            )
        return save_prefs(current, user_id=uid)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"auth.notification_prefs.update_pref: failed: {exc}"
        )
        return False


def reset_prefs(*, user_id: str) -> bool:
    """Delete the user's prefs row so subsequent ``get_prefs`` calls
    return defaults. NEVER raises.

    Returns ``True`` even if the row didn't exist — "the row is gone"
    is true either way. ``False`` only on a persistence failure.
    """
    try:
        from state.db import get_connection

        uid = _coerce_user_id(user_id)
        conn = get_connection()
        with conn:
            conn.execute(
                "DELETE FROM kv_state WHERE key = ?", (_row_key(uid),)
            )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"auth.notification_prefs.reset_prefs: failed: {exc}"
        )
        return False


# ─── Filter helper (the meat) ─────────────────────────────────────────────

def _is_in_quiet_window(window: tuple[int, int], hour: int) -> bool:
    """Return ``True`` iff ``hour`` is inside ``(start, end)``.

    Normal window (start < end): ``start <= hour < end``.
    Wraparound window (start > end): ``hour >= start OR hour < end``.
    Degenerate window (start == end): ``False`` — treat as "not
      configured" to avoid silencing 24/7 from a half-edited config.
    """
    start, end = window
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    # Wraparound
    return hour >= start or hour < end


def _meets_min_severity(alert_severity: str, min_severity: str) -> bool:
    """True iff ``alert_severity`` is at or above the ``min_severity``
    floor. Uses the HIGH-NUMBER-IS-WORSE rank (CRITICAL=4 ≥ HIGH=3 ≥
    MEDIUM=2 ≥ LOW=1). An unknown severity (either side) collapses to
    LOW so a typo never gets a free pass."""
    a_rank = _SEVERITY_RANK.get(alert_severity, _SEVERITY_RANK["LOW"])
    floor = _SEVERITY_RANK.get(min_severity, _SEVERITY_RANK["LOW"])
    return a_rank >= floor


def _bump_suppressed_counter(amount: int = 1) -> None:
    """Increment the ``alerts_suppressed_by_prefs`` kv_state counter.

    Mirrors ``engine.alert_engine_v2._bump_suppressed_counter``. Best-
    effort — a write failure is swallowed because the counter is
    operator telemetry, not correctness.
    """
    if amount <= 0:
        return
    try:
        from state.db import get_connection

        conn = get_connection()
        now_iso = _now_iso()
        # Atomic increment-by-amount in ONE statement — no lost-update window
        # (the old SELECT-then-INSERT-OR-REPLACE under autocommit had one; note
        # `with conn:` is a no-op for transactions under isolation_level=None).
        # First write seeds the value at `amount`; later writes add it.
        # CAST(value AS INTEGER) yields 0 for a missing/corrupt value.
        conn.execute(
            "INSERT INTO kv_state (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "value = CAST(value AS INTEGER) + ?, "
            "updated_at = excluded.updated_at",
            (_PREFS_SUPPRESSED_KEY, str(amount), now_iso, amount),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"auth.notification_prefs._bump_suppressed_counter: "
            f"kv_state write failed: {exc}"
        )


def get_suppressed_by_prefs_count() -> int:
    """Return the cumulative count of channel-drops suppressed by user
    prefs since the counter started (or since a manual reset). Returns
    ``0`` when the kv_state row does not yet exist. NEVER raises."""
    try:
        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM kv_state WHERE key = ?",
            (_PREFS_SUPPRESSED_KEY,),
        ).fetchone()
        if row is None:
            return 0
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            return 0
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"auth.notification_prefs.get_suppressed_by_prefs_count: "
            f"read failed: {exc}"
        )
        return 0


def filter_channels_by_prefs(
    channels: list,
    alert: Any,
    *,
    user_id: str,
    now_utc: Optional[datetime] = None,
) -> list:
    """Apply :class:`NotificationPrefs` to a candidate list of channels.

    Returns the filtered subset. NEVER raises — any internal failure
    short-circuits to "return ``channels`` unchanged" so a prefs bug
    cannot silently drop deliveries.

    Rules (in order):
      1. ``prefs.enabled is False`` → ``[]``.
      2. ``alert.severity < prefs.min_severity`` → ``[]``.
      3. ``prefs.alert_type_filter`` non-empty AND
         ``alert.alert_type not in it`` → ``[]``.
      4. ``prefs.quiet_during_hours`` set AND current UTC hour in window
         → ``[]`` UNLESS the alert is CRITICAL — CRITICAL always passes.
      5. ``prefs.severity_channel_map`` has entry for
         ``alert.severity`` → return only channels whose ``channel_id``
         is in the map's list. Otherwise return ``channels`` unchanged.
    """
    try:
        # Empty input — nothing to filter.
        if not channels:
            return []

        uid = _coerce_user_id(user_id)
        prefs = get_prefs(user_id=uid)

        # 1) Master switch.
        if not prefs.enabled:
            return []

        # Read alert fields defensively — the caller may pass anything
        # that quacks like a ShippingAlert. Missing fields default to
        # the most permissive option so a malformed alert isn't
        # silently dropped by the filter.
        severity = getattr(alert, "severity", "") or ""
        alert_type = getattr(alert, "alert_type", "") or ""

        # 2) Min-severity floor.
        if not _meets_min_severity(severity, prefs.min_severity):
            return []

        # 3) Alert-type allow-list.
        if prefs.alert_type_filter and alert_type not in prefs.alert_type_filter:
            return []

        # 4) Quiet hours. CRITICAL bypasses.
        if prefs.quiet_during_hours is not None and severity != "CRITICAL":
            now = now_utc if now_utc is not None else datetime.now(timezone.utc)
            try:
                hour = int(now.hour)
            except Exception:
                hour = 0
            if _is_in_quiet_window(prefs.quiet_during_hours, hour):
                return []

        # 5) Per-severity channel allow-list. Only applied when the
        # map has an entry for the current alert's severity.
        allow = prefs.severity_channel_map.get(severity)
        if allow is not None:
            allow_set = {c for c in allow if isinstance(c, str)}
            return [
                c for c in channels
                if isinstance(getattr(c, "channel_id", None), str)
                and getattr(c, "channel_id") in allow_set
            ]

        return list(channels)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"auth.notification_prefs.filter_channels_by_prefs: "
            f"failed (returning input unchanged): {exc}"
        )
        return list(channels) if channels else []


__all__ = [
    "NotificationPrefs",
    "filter_channels_by_prefs",
    "get_prefs",
    "get_suppressed_by_prefs_count",
    "reset_prefs",
    "save_prefs",
    "update_pref",
]
