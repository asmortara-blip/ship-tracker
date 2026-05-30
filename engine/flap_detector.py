"""Anti-flapping detector for AlertRules (v19).

A metric oscillating around a threshold (BDI bouncing in/out of a 5%
move band, a freight rate hovering at the trigger line) used to produce
a fire / resolve / fire / resolve cascade — one alert row per crossing,
filling the feed with the same condition flapping back and forth.

This module records every threshold crossing into a per-rule sliding
window stored in ``kv_state`` (so no schema bump is needed), and lets
``fire_rule`` consult :func:`is_flapping` to decide whether to emit a
normal alert or fold the crossings into ONE consolidated "rule is
flapping" alert.

Storage layout
--------------
One ``kv_state`` row per (user, rule), keyed
``flap_window:<user_id>:<rule_id>``. The value is a JSON object:

    {
        "crossings": ["2026-05-22T15:00:00+00:00", ...],
        "flap_alert_emitted_at": "2026-05-22T15:30:00+00:00"  // optional
    }

``crossings`` holds the ISO-8601 timestamps of every crossing recorded.
On every read the list is pruned to the configured window before the
length is consulted, so old crossings naturally fall out without a
separate sweep job.

``flap_alert_emitted_at`` is set by :func:`mark_flap_alert_emitted`
the first time a consolidated flap alert is created within the current
window. While it is set, :func:`should_emit_flap_alert` returns
False — subsequent flap-detected fires inside the same window get
silently counted toward ``flap_suppressed_counter`` instead of
emitting another flap alert. The mark is cleared automatically once
every crossing it was based on has aged out of the window.

Orthogonality
-------------
Flapping is orthogonal to cooldown (``_in_cooldown``) and to the v14
dedup_key collapse. Cooldown stops the SAME rule from re-firing inside
a window; dedup merges bounces of an alert that ALREADY fired into
one row by incrementing ``fire_count``. Flapping watches for the
oscillation pattern across MANY fire/resolve events and consolidates
them into a single explanatory alert.

The detector is OPT-IN — rules without ``flap_detection_enabled=True``
never call into this module, preserving every legacy behaviour.

NEVER raises
------------
Every public helper is wrapped to swallow exceptions and return a safe
default. A flap detector that silently misses an oscillation is far
better than one that crashes the alert engine.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from loguru import logger


# ─────────────────────────────────────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────────────────────────────────────

# kv_state key prefix. Per-user-per-rule scoping is encoded in the key
# itself: ``flap_window:<user_id>:<rule_id>``. The empty-user-id case
# (legacy / pre-auth callers) keys to ``flap_window::<rule_id>``.
_KEY_PREFIX: str = "flap_window:"

# Counter of fires that the flap detector silently swallowed (every
# crossing inside an already-flapping window beyond the first
# consolidated alert). Telemetry only — surfaced in the operator
# overview alongside the cooldown-suppressed counter.
_FLAP_SUPPRESSED_KEY: str = "alerts_suppressed_by_flap"


# ─────────────────────────────────────────────────────────────────────────────
#  Dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FlapWindow:
    """A snapshot of one rule's crossing history within the window.

    Returned by :func:`get_flap_window` and :func:`get_flapping_rules`.
    The list of crossings has already been pruned to the requested
    ``window_minutes`` by the time the dataclass is constructed.
    """
    rule_id: str
    crossings: int                              # number of crossings in window
    first_crossing_at: str                       # ISO, earliest crossing in window
    last_crossing_at: str                        # ISO, most recent crossing in window
    suppressed_alert_ids: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_user_id(user_id: Optional[str]) -> str:
    """Mirror of ``alert_engine_v2._resolve_user_id``.

    ``None`` means "caller did not specify" → consult the session via
    ``current_user_id()``. An explicit empty string means "legacy
    bucket" and is returned as-is. Never raises.
    """
    if user_id is None:
        try:
            from state.user_scope import current_user_id
            return current_user_id() or ""
        except Exception:
            return ""
    return user_id


def _row_key(user_id: str, rule_id: str) -> str:
    """The kv_state row key for ``(user_id, rule_id)``."""
    return f"{_KEY_PREFIX}{user_id}:{rule_id}"


def _load_blob(user_id: str, rule_id: str) -> dict:
    """Read the per-(user, rule) flap blob from kv_state.

    Returns ``{}`` when the row is missing, the JSON fails to parse,
    or any DB read error occurs. NEVER raises — the alert engine
    treats the absence of a blob as "no crossings recorded".
    """
    try:
        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM kv_state WHERE key = ?",
            (_row_key(user_id, rule_id),),
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
            f"flap_detector._load_blob: read failed for "
            f"user_id={user_id!r} rule_id={rule_id!r}: {exc}"
        )
        return {}


def _save_blob(user_id: str, rule_id: str, blob: dict) -> bool:
    """Persist the per-(user, rule) flap blob to kv_state. Returns success."""
    try:
        from state.db import get_connection

        payload = json.dumps(blob, default=str)
        now = _now_iso()
        conn = get_connection()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_state (key, value, updated_at) "
                "VALUES (?, ?, ?)",
                (_row_key(user_id, rule_id), payload, now),
            )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"flap_detector._save_blob: write failed for "
            f"user_id={user_id!r} rule_id={rule_id!r}: {exc}"
        )
        return False


def _delete_blob(user_id: str, rule_id: str) -> bool:
    """Remove the per-(user, rule) flap blob. Returns success."""
    try:
        from state.db import get_connection

        conn = get_connection()
        with conn:
            conn.execute(
                "DELETE FROM kv_state WHERE key = ?",
                (_row_key(user_id, rule_id),),
            )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"flap_detector._delete_blob: delete failed for "
            f"user_id={user_id!r} rule_id={rule_id!r}: {exc}"
        )
        return False


def _prune(crossings: list, *, window_minutes: int, now: Optional[datetime] = None) -> list:
    """Drop crossings older than ``window_minutes`` from ``now``.

    Returns a NEW list — does not mutate the input. Non-string entries
    or unparseable ISO strings are dropped silently so a corrupt blob
    cannot wedge the prune path.
    """
    if window_minutes <= 0:
        # A non-positive window means "no window" — every crossing
        # ages out immediately. Return empty without scanning.
        return []
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(minutes=window_minutes)
    kept: list[str] = []
    for ts in crossings:
        if not isinstance(ts, str) or not ts:
            continue
        try:
            parsed = datetime.fromisoformat(ts)
        except (TypeError, ValueError):
            continue
        # Defensive: a naive timestamp is treated as UTC so the
        # comparison stays valid even if a hand-edited blob stripped
        # the offset.
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if parsed >= cutoff:
            kept.append(ts)
    return kept


def _bump_flap_suppressed_counter() -> None:
    """Increment the kv_state counter of flap-suppressed fires.

    Best-effort: any failure is swallowed and logged. Mirrors the
    cooldown counter pattern in ``alert_engine_v2._bump_suppressed_counter``.
    """
    try:
        from state.db import get_connection

        conn = get_connection()
        now_iso = _now_iso()
        # Atomic increment in ONE statement — the old SELECT-then-INSERT-OR-
        # REPLACE had a lost-update window (two callers both read N, both
        # wrote N+1). CAST(value AS INTEGER) yields 0 for a missing/corrupt
        # value, matching the prior reset-to-0 fallback.
        conn.execute(
            "INSERT INTO kv_state (key, value, updated_at) VALUES (?, '1', ?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "value = CAST(value AS INTEGER) + 1, "
            "updated_at = excluded.updated_at",
            (_FLAP_SUPPRESSED_KEY, now_iso),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"flap_detector._bump_flap_suppressed_counter: write failed: {exc}"
        )


def get_flap_suppressed_count() -> int:
    """Return the cumulative count of fires the flap detector has
    silently swallowed since the counter was first written (or since
    last manual reset). Returns 0 when the row does not exist.
    """
    try:
        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM kv_state WHERE key = ?",
            (_FLAP_SUPPRESSED_KEY,),
        ).fetchone()
        if row is None:
            return 0
        return int(row["value"])
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"flap_detector.get_flap_suppressed_count: read failed: {exc}")
        return 0


# ─────────────────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────────────────

def record_threshold_crossing(
    rule_id: str,
    direction: str,
    *,
    user_id: Optional[str] = None,
) -> None:
    """Record one threshold crossing for ``rule_id`` under ``user_id``.

    ``direction`` is a free-form label ("fire" / "resolve" / "up" /
    "down") kept by callers to document INTENT; the detector itself
    only counts crossings regardless of direction (a flap is a flap
    whether the metric went up or down). The label is logged but not
    persisted into the blob — only timestamps are stored, which keeps
    the blob small and the prune fast.

    Empty/falsy ``rule_id`` is a no-op (defensive — callers should
    pass a real rule_id but a missing one should not crash the
    engine). NEVER raises.
    """
    if not rule_id:
        return
    try:
        uid = _resolve_user_id(user_id)
        blob = _load_blob(uid, rule_id)
        crossings = blob.get("crossings")
        if not isinstance(crossings, list):
            crossings = []
        crossings.append(_now_iso())
        blob["crossings"] = crossings
        _save_blob(uid, rule_id, blob)
        logger.debug(
            f"flap_detector: recorded crossing rule_id={rule_id!r} "
            f"direction={direction!r} user_id={uid!r} (total={len(crossings)})"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"flap_detector.record_threshold_crossing: failed for "
            f"rule_id={rule_id!r}: {exc}"
        )


def is_flapping(
    rule_id: str,
    *,
    window_minutes: int = 30,
    threshold_crossings: int = 5,
    user_id: Optional[str] = None,
) -> bool:
    """Return True when ``rule_id`` has crossed its threshold at least
    ``threshold_crossings`` times in the last ``window_minutes`` (under
    ``user_id``).

    The check prunes old crossings on read — crossings older than the
    window naturally drop out without a sweep. NEVER raises; any
    failure returns False (fail-open: a broken flap detector should
    not silently block legitimate alerts).
    """
    if not rule_id:
        return False
    if threshold_crossings <= 0:
        # Defensive: a non-positive threshold would mean "always
        # flapping" which is never the intent. Treat as "disabled".
        return False
    try:
        uid = _resolve_user_id(user_id)
        blob = _load_blob(uid, rule_id)
        crossings = blob.get("crossings")
        if not isinstance(crossings, list):
            return False
        kept = _prune(crossings, window_minutes=window_minutes)
        return len(kept) >= threshold_crossings
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"flap_detector.is_flapping: failed for rule_id={rule_id!r}: {exc}"
        )
        return False


def get_flap_window(
    rule_id: str,
    *,
    window_minutes: int = 30,
    user_id: Optional[str] = None,
) -> Optional[FlapWindow]:
    """Return a :class:`FlapWindow` snapshot of the rule's recent
    crossings, or ``None`` when no crossings exist inside the window.

    The returned ``crossings`` field is the COUNT of crossings in
    window; the full timestamp list is not exposed (callers that need
    the timestamps should reach into the blob via the kv_state path,
    but the public contract is the count + first/last).

    NEVER raises — returns None on any failure.
    """
    if not rule_id:
        return None
    try:
        uid = _resolve_user_id(user_id)
        blob = _load_blob(uid, rule_id)
        crossings = blob.get("crossings")
        if not isinstance(crossings, list):
            return None
        kept = _prune(crossings, window_minutes=window_minutes)
        if not kept:
            return None
        # ISO-8601 UTC strings are lexicographically orderable, so
        # min/max on the list match chronological order without parsing.
        return FlapWindow(
            rule_id=rule_id,
            crossings=len(kept),
            first_crossing_at=min(kept),
            last_crossing_at=max(kept),
            suppressed_alert_ids=[],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"flap_detector.get_flap_window: failed for rule_id={rule_id!r}: {exc}"
        )
        return None


def reset_flap_window(
    rule_id: str,
    *,
    user_id: Optional[str] = None,
) -> bool:
    """Clear the crossing history for ``rule_id`` under ``user_id``.

    Returns True when the row was deleted (or did not exist to begin
    with — both are "the rule is no longer marked as flapping"). NEVER
    raises.
    """
    if not rule_id:
        return False
    try:
        uid = _resolve_user_id(user_id)
        return _delete_blob(uid, rule_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"flap_detector.reset_flap_window: failed for rule_id={rule_id!r}: {exc}"
        )
        return False


def get_flapping_rules(
    *,
    window_minutes: int = 30,
    threshold_crossings: int = 5,
    user_id: Optional[str] = None,
) -> list[FlapWindow]:
    """Return one :class:`FlapWindow` per rule that is currently flapping.

    Scans every ``flap_window:*`` row in kv_state, prunes its
    crossings to ``window_minutes``, and yields a FlapWindow for any
    rule whose pruned count >= ``threshold_crossings``.

    When ``user_id`` is supplied (including an explicit empty string),
    only rows under that user are considered. ``user_id=None`` (the
    default sentinel) consults the Streamlit session via
    ``_resolve_user_id`` for the active user; tests that want the
    cross-user view should pass ``user_id=""`` explicitly to mean "the
    legacy bucket" or wrap the call to enumerate users.

    NEVER raises — returns ``[]`` on any failure.
    """
    try:
        from state.db import get_connection

        conn = get_connection()
        # The LIKE filter on the key prefix is index-friendly (PRIMARY
        # KEY → BTREE on key) and cheap. We always need to inspect the
        # value to count crossings, so a SELECT *-like fetch is fine.
        rows = conn.execute(
            "SELECT key, value FROM kv_state WHERE key LIKE ?",
            (f"{_KEY_PREFIX}%",),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"flap_detector.get_flapping_rules: SQLite read failed: {exc}"
        )
        return []

    # Resolve the per-user filter once. The sentinel ``None`` means
    # "active session user" (mirrors _resolve_user_id), so explicitly
    # check whether the caller passed a value to differentiate
    # "cross-user view requested" from "scope to active user".
    filter_uid = _resolve_user_id(user_id) if user_id is not None else None

    out: list[FlapWindow] = []
    for row in rows:
        key = row["key"] if hasattr(row, "keys") else row[0]
        raw = row["value"] if hasattr(row, "keys") else row[1]
        # Key shape: ``flap_window:<user_id>:<rule_id>``. Split on the
        # first two colons only — a rule_id is free-form and may itself
        # contain colons; the user_id (from auth) does not.
        rest = key[len(_KEY_PREFIX):]
        # The prefix already consumed; rest is "<user_id>:<rule_id>".
        # Split on the FIRST colon to recover the two parts.
        if ":" not in rest:
            continue
        uid, rule_id = rest.split(":", 1)
        if filter_uid is not None and uid != filter_uid:
            continue
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(parsed, dict):
            continue
        crossings = parsed.get("crossings")
        if not isinstance(crossings, list):
            continue
        kept = _prune(crossings, window_minutes=window_minutes)
        if len(kept) < threshold_crossings:
            continue
        out.append(FlapWindow(
            rule_id=rule_id,
            crossings=len(kept),
            first_crossing_at=min(kept),
            last_crossing_at=max(kept),
            suppressed_alert_ids=[],
        ))
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  Consolidation helpers (used by alert_engine_v2.fire_rule)
# ─────────────────────────────────────────────────────────────────────────────

def should_emit_flap_alert(
    rule_id: str,
    *,
    window_minutes: int = 30,
    user_id: Optional[str] = None,
) -> bool:
    """Return True when a fresh consolidated flap alert should be created.

    The flap-alert contract is ONE alert per window. The first time a
    rule trips :func:`is_flapping`, an alert is created and
    :func:`mark_flap_alert_emitted` stamps the blob with the emission
    timestamp. While that stamp is set AND falls inside the active
    window, this helper returns False — subsequent flap-detected fires
    are silently swallowed and counted toward the
    ``flap_suppressed_counter``.

    The stamp is auto-cleared once no surviving crossing precedes it
    (i.e. the window that originally triggered the consolidation has
    fully aged out). The next time the rule trips again, a fresh
    consolidated alert is emitted.

    NEVER raises — returns False on any failure (fail-open: better to
    suppress one alert than to crash).
    """
    if not rule_id:
        return False
    try:
        uid = _resolve_user_id(user_id)
        blob = _load_blob(uid, rule_id)
        stamp = blob.get("flap_alert_emitted_at")
        if not stamp or not isinstance(stamp, str):
            return True
        # If the stamp itself is older than the window, clear it and
        # allow a fresh consolidated alert. This keeps the "one per
        # window" guarantee honest across multiple flap episodes.
        try:
            parsed = datetime.fromisoformat(stamp)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            # Corrupt stamp → treat as "no stamp" and allow emission.
            return True
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
        if parsed < cutoff:
            # Stamp aged out — clear it and emit a fresh alert.
            blob.pop("flap_alert_emitted_at", None)
            _save_blob(uid, rule_id, blob)
            return True
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"flap_detector.should_emit_flap_alert: failed for "
            f"rule_id={rule_id!r}: {exc}"
        )
        return False


def mark_flap_alert_emitted(
    rule_id: str,
    *,
    user_id: Optional[str] = None,
) -> None:
    """Stamp the blob to record that a consolidated flap alert was
    emitted just now. Subsequent calls to :func:`should_emit_flap_alert`
    within the active window will return False.

    NEVER raises.
    """
    if not rule_id:
        return
    try:
        uid = _resolve_user_id(user_id)
        blob = _load_blob(uid, rule_id)
        blob["flap_alert_emitted_at"] = _now_iso()
        _save_blob(uid, rule_id, blob)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"flap_detector.mark_flap_alert_emitted: failed for "
            f"rule_id={rule_id!r}: {exc}"
        )


def bump_flap_suppressed_counter() -> None:
    """Public wrapper so ``alert_engine_v2.fire_rule`` can bump the
    counter without reaching into the underscore-prefixed internal."""
    _bump_flap_suppressed_counter()
