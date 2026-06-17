"""Per-route alert threshold overrides for the rate-surge detector.

The global ``check_rate_alerts(freight_data, threshold_pct=8.0)`` applies a
single threshold to every route. That breaks down in practice: transpacific
eastbound is volatile (a 10% weekly move is barely newsworthy), while
transatlantic is stable (a 5% move there matters). This module lets the
user tighten or loosen the threshold per route, and pin a severity tier
to specific routes regardless of move magnitude.

Storage
-------
Overrides live as a single JSON blob in the existing ``kv_state`` table
under the key ``route_thresholds``. No schema bump — one SELECT to read,
one INSERT OR REPLACE to write. The blob is a dict keyed by route_id:

    {
        "transpacific_eb": {"threshold_pct": 3.0, "severity": "HIGH"},
        "transatlantic":   {"threshold_pct": 15.0, "severity": "CRITICAL"}
    }

Failure modes
-------------
Every public function is best-effort: load returns ``{}`` on read /
parse failure; save returns ``False`` on write failure; neither raises.
The caller (``check_rate_alerts``) treats "no override" as "use the
function-level default" so the rate detector remains backward
compatible.

Audit
-----
``save_route_thresholds`` records an ``save_route_thresholds`` audit
event with ``{"count": N}`` so security review can see when the
override set was rewritten.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

from loguru import logger


# ─── Constants ─────────────────────────────────────────────────────────────

# The single kv_state key under which the entire override blob lives.
_KV_KEY: str = "route_thresholds"

# Allowed severity values for a RouteThreshold. Anything else is coerced
# back to the default at load time so a malformed blob cannot inject a
# bogus severity string into the alert pipeline.
_ALLOWED_SEVERITY: frozenset[str] = frozenset({"CRITICAL", "HIGH", "MEDIUM", "LOW"})

# Default severity when the caller does not specify one. HIGH matches the
# rate-detector's default severity for an over-threshold move, so the
# typical "I just want a tighter threshold" override behaves exactly
# like today without the user thinking about severity at all.
_DEFAULT_SEVERITY: str = "HIGH"


# ─── Dataclass ─────────────────────────────────────────────────────────────

@dataclass
class RouteThreshold:
    """Per-route override for the rate-surge alert detector.

    Fields:
        route_id:      The freight_data dict key this override applies to.
                       Must match the key the loader uses for the route.
        threshold_pct: % move (7-day) that triggers the alert. Replaces
                       the function-level ``threshold_pct`` parameter for
                       this route only.
        severity:      One of CRITICAL / HIGH / MEDIUM / LOW. When the
                       override fires, this severity tier is applied
                       regardless of move magnitude (so a noisy route's
                       move that would normally be CRITICAL can be
                       pinned to HIGH, and vice versa).
    """
    route_id: str
    threshold_pct: float
    severity: str = _DEFAULT_SEVERITY


# ─── Internal helpers ──────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_severity(value: object) -> str:
    """Coerce a raw severity value to one of _ALLOWED_SEVERITY.

    Anything outside the allowed set falls back to _DEFAULT_SEVERITY.
    Used at load time so a corrupted blob cannot inject e.g.
    ``severity='OMG'`` into the alert pipeline.
    """
    if isinstance(value, str) and value in _ALLOWED_SEVERITY:
        return value
    return _DEFAULT_SEVERITY


def _row_to_threshold(route_id: str, row: dict) -> RouteThreshold | None:
    """Build a RouteThreshold from one entry in the JSON blob.

    Returns None when the row is malformed (missing threshold_pct, or
    threshold_pct that does not coerce to float). The caller drops these
    silently — we never want one bad row to invalidate the whole set.
    """
    if not isinstance(row, dict):
        return None
    raw = row.get("threshold_pct")
    if raw is None:
        return None
    try:
        threshold_pct = float(raw)
    except (TypeError, ValueError):
        return None
    severity = _normalize_severity(row.get("severity"))
    return RouteThreshold(
        route_id=str(route_id),
        threshold_pct=threshold_pct,
        severity=severity,
    )


# ─── Public API: load / save ───────────────────────────────────────────────

def load_route_thresholds() -> dict[str, RouteThreshold]:
    """Load the per-route threshold overrides from kv_state.

    Returns a dict keyed by ``route_id``. An empty dict means "no
    overrides configured" — the rate detector will use its function-level
    default for every route. Any of the following return ``{}``:

      - the kv_state row does not exist
      - the row's ``value`` is not valid JSON
      - the parsed JSON is not a dict (e.g. someone hand-edited it
        into a list)
      - the SQLite read itself fails (connection error, DB locked, etc.)

    Malformed entries WITHIN a valid dict are silently dropped — the
    rest of the overrides still load. This matches the philosophy of
    ``load_rules``: never let one bad row break the whole pipeline.
    """
    try:
        from state.db import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM kv_state WHERE key = ?",
            (_KV_KEY,),
        ).fetchone()
        if row is None:
            return {}
        try:
            parsed = json.loads(row["value"])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.debug(f"route_thresholds: malformed JSON, returning empty: {exc}")
            return {}
        if not isinstance(parsed, dict):
            logger.debug(
                f"route_thresholds: expected dict at top level, got "
                f"{type(parsed).__name__}; returning empty"
            )
            return {}
        out: dict[str, RouteThreshold] = {}
        for route_id, entry in parsed.items():
            rt = _row_to_threshold(str(route_id), entry)
            if rt is not None:
                out[str(route_id)] = rt
        return out
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.debug(f"route_thresholds: load failed: {exc}")
        return {}


def save_route_thresholds(thresholds: dict[str, RouteThreshold]) -> bool:
    """Persist the per-route overrides to kv_state. Returns True / False.

    Encodes the dict as a single JSON blob under the key
    ``route_thresholds``. INSERT OR REPLACE upserts the row atomically.
    An empty dict is a valid input — it wipes any previously saved
    overrides (the encoded blob becomes ``{}``).

    Never raises. Returns False when:
      - the input is not a dict
      - the JSON encode fails
      - the SQLite write fails

    Side effect: records an ``save_route_thresholds`` audit event with
    ``{"count": len(thresholds)}`` so security review can see when the
    override set was rewritten.
    """
    if not isinstance(thresholds, dict):
        logger.debug(
            f"route_thresholds: save expects dict, got "
            f"{type(thresholds).__name__}"
        )
        return False
    try:
        payload = {
            str(route_id): {
                "threshold_pct": float(rt.threshold_pct),
                "severity": _normalize_severity(rt.severity),
            }
            for route_id, rt in thresholds.items()
            if isinstance(rt, RouteThreshold)
        }
        blob = json.dumps(payload, sort_keys=True)
    except (TypeError, ValueError) as exc:
        logger.debug(f"route_thresholds: encode failed: {exc}")
        return False

    ok = False
    try:
        from state.db import get_connection
        conn = get_connection()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_state (key, value, updated_at) "
                "VALUES (?, ?, ?)",
                (_KV_KEY, blob, _now_iso()),
            )
        ok = True
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.debug(f"route_thresholds: SQLite write failed: {exc}")
        ok = False

    # Audit-log the save. count records the number of overrides the
    # caller asked to persist, not the number that made it through the
    # filter — security review wants to see "user saved N overrides",
    # not "the encoder kept N of them". Wrapped in try/except as belt-
    # and-braces (record_audit never raises but we sit on a UI path).
    try:
        from auth.audit import record_audit
        record_audit(
            "save_route_thresholds",
            detail={"count": len(thresholds)},
        )
    except Exception:  # noqa: BLE001
        pass

    return ok


def get_threshold_for_route(route_id: str, default: float = 8.0) -> float:
    """Convenience lookup: return the override threshold_pct for a route,
    or ``default`` if no override exists.

    Loads the full override set from kv_state (one SELECT) and pulls the
    single route. Callers that need multiple lookups in a tight loop
    should ``load_route_thresholds()`` once and dict-index themselves —
    this helper is for one-off / UI display use.
    """
    overrides = load_route_thresholds()
    rt = overrides.get(str(route_id))
    if rt is None:
        return float(default)
    return float(rt.threshold_pct)


@dataclass
class ThresholdWriteResult:
    """Outcome of a single-key threshold write, carrying enough to undo it.

    Fields:
        ok:         Whether the write was persisted (the save returned True).
        key:        The store key written (a route_id OR an alert_type — the
                    store is a generic key→threshold map; see
                    ``set_threshold_for_key``).
        new_value:  The threshold_pct that was written.
        prior_value: The threshold_pct that was in the store *before* this
                    write, or None if no override existed for the key. This
                    is what makes the write reversible — to undo, call
                    ``set_threshold_for_key(key, prior_value)`` (or delete
                    the override when prior_value is None).
        had_prior:  True iff an override already existed for the key. Lets the
                    caller distinguish "prior was None because absent" from a
                    legitimately stored value.
    """
    ok: bool
    key: str
    new_value: float
    prior_value: float | None = None
    had_prior: bool = False


def set_threshold_for_key(
    key: str,
    threshold_pct: float,
    *,
    severity: str | None = None,
) -> ThresholdWriteResult:
    """Best-effort, validated, reversible single-key threshold write.

    The override store (``kv_state['route_thresholds']``) is a generic
    ``key → RouteThreshold`` map. The key was historically a ``route_id``;
    this helper makes it explicit that any opaque string key works — the
    self-tuning loop (``engine.alert_backtest.apply_recommended_threshold``)
    keys it by ``alert_type`` so a recommended STOCK_MOVE / BDI_MOVE /
    RATE_SURGE threshold lands in the same store the rate detector already
    reads.

    Validation:
      - ``key`` must be a non-empty string after ``str().strip()``.
      - ``threshold_pct`` must coerce to a finite float. NaN / inf / a
        non-numeric value → no write, ``ok=False``.
      - ``severity`` (when given) is normalized via ``_normalize_severity``;
        an unknown severity falls back to the default rather than rejecting
        the write. When ``severity`` is None the existing override's severity
        is preserved (or the default when the key is new).

    Reversibility: the returned ``ThresholdWriteResult`` carries the prior
    threshold_pct (``prior_value`` / ``had_prior``) so the caller can restore
    the previous state. Never raises — a load/encode/write failure returns
    ``ok=False`` with the prior captured when it was readable.
    """
    skey = str(key).strip() if key is not None else ""
    # Validate the value up front — never write NaN / inf / junk.
    try:
        value = float(threshold_pct)
    except (TypeError, ValueError):
        logger.debug(f"set_threshold_for_key: non-numeric threshold for {skey!r}")
        return ThresholdWriteResult(ok=False, key=skey, new_value=0.0)
    if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
        logger.debug(f"set_threshold_for_key: non-finite threshold for {skey!r}")
        return ThresholdWriteResult(ok=False, key=skey, new_value=value)
    if not skey:
        logger.debug("set_threshold_for_key: empty key rejected")
        return ThresholdWriteResult(ok=False, key=skey, new_value=value)

    # Capture the prior so the write is reversible.
    overrides = load_route_thresholds()
    prior_rt = overrides.get(skey)
    prior_value = float(prior_rt.threshold_pct) if prior_rt is not None else None
    had_prior = prior_rt is not None

    # Preserve the existing severity unless the caller overrides it.
    if severity is not None:
        new_severity = _normalize_severity(severity)
    elif prior_rt is not None:
        new_severity = _normalize_severity(prior_rt.severity)
    else:
        new_severity = _DEFAULT_SEVERITY

    overrides[skey] = RouteThreshold(
        route_id=skey, threshold_pct=value, severity=new_severity,
    )
    ok = save_route_thresholds(overrides)
    return ThresholdWriteResult(
        ok=bool(ok),
        key=skey,
        new_value=value,
        prior_value=prior_value,
        had_prior=had_prior,
    )


__all__ = [
    "RouteThreshold",
    "ThresholdWriteResult",
    "load_route_thresholds",
    "save_route_thresholds",
    "get_threshold_for_route",
    "set_threshold_for_key",
]
