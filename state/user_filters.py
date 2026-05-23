"""state.user_filters — per-user saved filter presets.

Design
------
The app has filter controls on many tabs — severity dropdowns on the
alert center, window selectboxes on report tabs, route selectors on the
voyage tracker — and users tend to come back to the same combinations
("everything CRITICAL in the last 24h", "transpacific-only", "just my
ZIM tracker"). Saved filter presets let them name a combination once and
recall it from anywhere.

This module owns the persistence side of that capability:

* :class:`FilterPreset` — the dataclass shape callers consume. Carries
  the preset ``name``, a ``scope`` string (``'alerts'`` / ``'reports'`` /
  ``'dashboard'`` / ...) that prevents presets from different surfaces
  from colliding on the same name, and a free-form ``payload`` dict of
  filter values.
* :func:`save_preset` — upsert one preset into the user's saved-filter
  list. INSERT-OR-REPLACEs the row keyed by ``user_filters:{user_id}``
  in the ``kv_state`` table. NEVER raises.
* :func:`load_presets` — return the user's presets, optionally filtered
  by scope. NEVER raises.
* :func:`get_preset` — by-name lookup. NEVER raises.
* :func:`delete_preset` — remove one preset by name+scope. NEVER raises.

Storage
-------
Everything is stored as a single JSON blob per user in the existing
``kv_state`` table under the key ``user_filters:{user_id}``. The blob is
a JSON object mapping ``f"{scope}:{name}"`` to the preset record. No
schema bump — ``kv_state`` is the single-row JSON-blob store used for
other small per-user state (see ``state/vault.py`` for the same pattern).

Audit
-----
:func:`save_preset` and :func:`delete_preset` each record one audit
event via ``auth.audit.record_audit``. The detail payload carries the
preset ``name`` and ``scope`` ONLY — the filter ``payload`` itself is
deliberately NOT logged. A user's saved filters can encode sensitive
combinations (e.g. "only show me alerts for ticker XYZ"); the audit log
records the FACT of save/delete without leaking the criteria.

What this module does NOT do
----------------------------
* No UI wiring. Hooking saved-filter dropdowns into ``ui/tab_alerts.py``
  etc. is a follow-up commit.
* No global / shared filter library. Presets are strictly per-user.
* No cross-user reads or admin views.
* No schema bump. The kv_state JSON blob is the only persistence layer.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger


# ─── Storage key ───────────────────────────────────────────────────────────

# The kv_state row holding the user's saved-filter dict. One row per user
# keyed by user_id; the value is a JSON object of presets. The empty-
# string user_id ("no user" legacy bucket) gets its own row at
# ``user_filters:`` — separate from any signed-in user, so anonymous
# session state cannot bleed into an authenticated user's presets.
_KEY_PREFIX: str = "user_filters:"


# ─── Data ──────────────────────────────────────────────────────────────────

@dataclass
class FilterPreset:
    """One saved filter combination.

    Fields:
      * ``name``    — human-readable label the user chose
        (e.g. ``"all-CRITICAL-last-24h"``). Used as half of the
        composite key (``scope`` + ``name`` together uniquely identify
        a preset for a given user).
      * ``scope``   — which surface this preset belongs to
        (e.g. ``"alerts"``, ``"reports"``, ``"dashboard"``). Two presets
        on different surfaces can share the same name without colliding.
      * ``payload`` — the dict of filter values. Free-form on purpose —
        each surface defines its own filter vocabulary, and storing the
        payload as an opaque dict lets new filter controls ship without
        changing this module.
    """
    name: str
    scope: str
    payload: dict[str, Any] = field(default_factory=dict)


# ─── Internal helpers ──────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_user_id(user_id: Optional[str]) -> str:
    """Pick the user_id to scope the preset row to.

    Explicit ``user_id`` (when non-None) wins over the session lookup
    so tests can pin a known id without standing up a Streamlit
    session_state. Mirrors ``auth.audit._resolve_user_id``.
    """
    if user_id is not None:
        if not isinstance(user_id, str):
            return ""
        return user_id
    try:
        from state.user_scope import current_user_id
        return current_user_id()
    except Exception:
        return ""


def _row_key(user_id: str) -> str:
    """The kv_state row key for ``user_id``'s preset blob."""
    return f"{_KEY_PREFIX}{user_id}"


def _preset_key(scope: str, name: str) -> str:
    """The composite key used inside the JSON blob.

    ``scope:name`` lets two presets share the same name across different
    surfaces without colliding (alerts:"24h" vs reports:"24h" are
    independent rows).
    """
    return f"{scope}:{name}"


def _load_blob(user_id: str) -> dict[str, dict[str, Any]]:
    """Read the user's preset blob from kv_state.

    Returns an empty dict when the row is missing OR when the JSON
    fails to parse. NEVER raises — callers expect this to always
    return a dict.
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
    except Exception as exc:  # noqa: BLE001 — generic catch by contract
        logger.debug(
            f"state.user_filters._load_blob: read failed for "
            f"user_id={user_id!r}: {exc}"
        )
        return {}


def _save_blob(user_id: str, blob: dict[str, dict[str, Any]]) -> bool:
    """Persist the user's preset blob to kv_state. Returns success."""
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
            f"state.user_filters._save_blob: write failed for "
            f"user_id={user_id!r}: {exc}"
        )
        return False


def _record_to_preset(record: Any) -> Optional[FilterPreset]:
    """Map a single preset record from the JSON blob to a FilterPreset.

    Returns ``None`` for a malformed record (missing fields, wrong
    types) so :func:`load_presets` can skip it instead of raising on
    one bad row.
    """
    if not isinstance(record, dict):
        return None
    name = record.get("name")
    scope = record.get("scope")
    payload = record.get("payload", {})
    if not isinstance(name, str) or not name:
        return None
    if not isinstance(scope, str) or not scope:
        return None
    if not isinstance(payload, dict):
        payload = {}
    return FilterPreset(name=name, scope=scope, payload=payload)


def _preset_to_record(preset: FilterPreset) -> dict[str, Any]:
    """Serialize a :class:`FilterPreset` to the dict shape stored in the
    JSON blob. The composite ``scope:name`` key lives in the blob's
    outer dict, not inside the record itself."""
    return {
        "name": preset.name,
        "scope": preset.scope,
        "payload": preset.payload if isinstance(preset.payload, dict) else {},
    }


# ─── Public API ───────────────────────────────────────────────────────────

def save_preset(
    preset: FilterPreset,
    *,
    user_id: Optional[str] = None,
) -> bool:
    """Persist ``preset`` to the user's saved-filter library.

    Args:
        preset:   The :class:`FilterPreset` to save. A non-FilterPreset
                  input returns ``False`` without touching the DB.
        user_id:  Explicit user id, or ``None`` to resolve from the
                  active Streamlit session via ``current_user_id``. The
                  empty string is the "no user" legacy bucket.

    Returns:
        ``True`` on success, ``False`` on any validation or persistence
        failure. NEVER raises.

    Behaviour:
      1. Validates the input. ``preset`` must be a :class:`FilterPreset`
         with non-empty ``name`` and ``scope``.
      2. Reads the user's current blob (or empty dict if none).
      3. INSERT-OR-REPLACEs the entry keyed by ``"{scope}:{name}"`` —
         saving a preset with the same name+scope OVERWRITES rather
         than duplicating.
      4. Writes the updated blob back to kv_state.
      5. Records an audit event whose ``detail`` carries
         ``{"name": ..., "scope": ...}`` — NOT the payload values.
    """
    try:
        if not isinstance(preset, FilterPreset):
            logger.debug(
                f"state.user_filters.save_preset: input is not a "
                f"FilterPreset (got {type(preset).__name__})"
            )
            return False
        if not isinstance(preset.name, str) or not preset.name:
            logger.debug(
                "state.user_filters.save_preset: missing name, refusing"
            )
            return False
        if not isinstance(preset.scope, str) or not preset.scope:
            logger.debug(
                "state.user_filters.save_preset: missing scope, refusing"
            )
            return False

        uid = _resolve_user_id(user_id)
        blob = _load_blob(uid)
        blob[_preset_key(preset.scope, preset.name)] = _preset_to_record(preset)
        ok = _save_blob(uid, blob)
        if not ok:
            return False

        # Audit-log the save. Best-effort — a failed audit-write must
        # NOT fail the save (the user's preset is already in the DB
        # at this point). The detail carries name+scope only — the
        # payload values are deliberately NOT logged because saved
        # filters can encode sensitive criteria (specific tickers,
        # ports, route ids the user is watching).
        try:
            from auth.audit import record_audit
            record_audit(
                "save_filter_preset",
                entity_type="filter_preset",
                entity_id=_preset_key(preset.scope, preset.name),
                detail={"name": preset.name, "scope": preset.scope},
                user_id=uid,
            )
        except Exception:  # noqa: BLE001
            pass
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"state.user_filters.save_preset: failed: {exc}"
        )
        return False


def load_presets(
    *,
    user_id: Optional[str] = None,
    scope: Optional[str] = None,
) -> list[FilterPreset]:
    """Return the user's saved presets.

    Args:
        user_id: Explicit user id, or ``None`` to resolve from the
                 active Streamlit session.
        scope:   When provided, return ONLY presets matching this
                 scope (e.g. ``"alerts"``). When ``None``, return every
                 preset the user has saved across every surface.

    Returns:
        A list of :class:`FilterPreset`. Empty list on any read error
        or for an unknown user. NEVER raises. Order is not guaranteed —
        callers that want a particular order should sort the result.
    """
    try:
        uid = _resolve_user_id(user_id)
        blob = _load_blob(uid)
        out: list[FilterPreset] = []
        for record in blob.values():
            preset = _record_to_preset(record)
            if preset is None:
                continue
            if scope is not None and preset.scope != scope:
                continue
            out.append(preset)
        return out
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"state.user_filters.load_presets: failed: {exc}"
        )
        return []


def get_preset(
    name: str,
    scope: str,
    *,
    user_id: Optional[str] = None,
) -> Optional[FilterPreset]:
    """Look up one preset by ``name`` + ``scope``.

    Returns the :class:`FilterPreset` when found, ``None`` when the
    preset does not exist or on any read error. NEVER raises.
    """
    try:
        if not isinstance(name, str) or not name:
            return None
        if not isinstance(scope, str) or not scope:
            return None
        uid = _resolve_user_id(user_id)
        blob = _load_blob(uid)
        record = blob.get(_preset_key(scope, name))
        if record is None:
            return None
        return _record_to_preset(record)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"state.user_filters.get_preset: failed for "
            f"name={name!r}, scope={scope!r}: {exc}"
        )
        return None


def delete_preset(
    name: str,
    scope: str,
    *,
    user_id: Optional[str] = None,
) -> bool:
    """Remove the preset identified by ``name`` + ``scope``.

    Returns:
        ``True`` when the preset existed and was removed.
        ``False`` when the preset did not exist OR on any persistence
        failure. NEVER raises.

    Records an audit event whose ``detail`` carries ``{"name": ...,
    "scope": ...}`` ONLY when an actual deletion happened — a no-op
    delete (preset never existed) does NOT generate an audit row.
    """
    try:
        if not isinstance(name, str) or not name:
            return False
        if not isinstance(scope, str) or not scope:
            return False

        uid = _resolve_user_id(user_id)
        blob = _load_blob(uid)
        composite = _preset_key(scope, name)
        if composite not in blob:
            return False
        del blob[composite]
        ok = _save_blob(uid, blob)
        if not ok:
            return False

        try:
            from auth.audit import record_audit
            record_audit(
                "delete_filter_preset",
                entity_type="filter_preset",
                entity_id=composite,
                detail={"name": name, "scope": scope},
                user_id=uid,
            )
        except Exception:  # noqa: BLE001
            pass
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"state.user_filters.delete_preset: failed for "
            f"name={name!r}, scope={scope!r}: {exc}"
        )
        return False


__all__ = [
    "FilterPreset",
    "save_preset",
    "load_presets",
    "get_preset",
    "delete_preset",
]
