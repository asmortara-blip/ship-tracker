"""source_health_alerts.py — auto-fire ShippingAlerts when a data feed degrades.

The companion to ``engine.source_health``: that module collects per-feed
liveness pings; this module reads the resulting summary and, when a
source has gone red (status='down' or stale beyond a threshold) or
yellow (status='degraded' or moderately stale), persists a
``ShippingAlert`` so the operator sees the failure without having to
scroll the data-health panel.

Design
------
1. ``check_source_health_and_fire()`` is the orchestrator. It reads the
   current ``get_health_summary()`` snapshot, walks each source, decides
   whether the source needs an alert, and fires via
   ``alert_engine_v2.save_alerts``. Returns
   ``{"fired": N, "skipped_cooldown": N, "errored": N}`` so callers
   (the scheduler, the CLI run-once handler) can report counts.

2. Cooldown: per-source-per-user, stored as an ISO timestamp in
   ``kv_state`` under key ``source_alert_cooldown:<user_id>:<source_id>``.
   The cooldown prevents a noisy feed from carpet-bombing the alert
   table — at most one alert per source per ``cooldown_minutes`` window.
   Cooldown is set ONLY after a successful fire (a save_alerts that
   raises does NOT mark the cooldown — let the next pass retry).

3. Per-source try/except inside the loop. One bad source (DB write
   blip, malformed summary entry) must NOT block the rest of the loop.
   Errored sources land in the ``errored`` counter and the loop
   continues. The orchestrator itself never raises — every
   exception path collapses to a populated count dict.

4. Recovery is intentionally NOT alerted. A source going from red →
   green produces NO alert — the operator already sees the green badge
   in the UI. Alerting on recovery would also fight the cooldown
   (the cooldown would mute it anyway, but firing-then-muting is
   wasted work).

5. Config persistence: ``SourceHealthAlertConfig`` is serialized to
   ``kv_state`` under ``source_health_alert_config:<user_id>`` (or
   ``source_health_alert_config:`` for the legacy global bucket when
   ``user_id`` is None or empty). ``load_config()`` / ``save_config()``
   round-trip the dataclass via JSON.

6. Lazy imports. ``engine.source_health`` and
   ``engine.alert_engine_v2`` are imported inside the functions, not at
   module load — keeps this module's import cost trivial for callers
   that only want the config dataclass.

What this module is NOT
-----------------------
* It does NOT mutate ``engine.source_health`` or
  ``engine.alert_engine_v2`` — it is a read-only consumer of both.
* It does NOT bypass the existing alert dedup machinery — every
  ShippingAlert is shoved through ``save_alerts`` with the standard
  ``_dedup_key`` shape, so two near-simultaneous fires for the same
  source collapse the way every other alert in the system collapses.
* It does NOT bump SCHEMA_VERSION. Config + cooldown both ride the
  existing ``kv_state`` table.
* It does NOT fire when ``config.enabled`` is False. The operator may
  deliberately disable auto-alerting (planned maintenance, known
  outage) and the loop must respect that.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from loguru import logger


# ─────────────────────────────────────────────────────────────────────────────
#  Config dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SourceHealthAlertConfig:
    """Operator-tunable knobs for auto-firing source-health alerts.

    Attributes
    ----------
    enabled:
        Master switch. When ``False``, ``check_source_health_and_fire``
        returns zero counts and writes nothing — the operator can
        deliberately quiet the loop during planned maintenance.
    red_threshold_minutes:
        A source whose latest ping is older than this many minutes
        (and not currently 'up') escalates to a CRITICAL alert. Also
        the threshold that escalates a 'down' status to CRITICAL
        regardless of staleness.
    yellow_threshold_minutes:
        A source whose latest ping is older than this many minutes
        but younger than ``red_threshold_minutes`` (or has 'degraded'
        status) gets a HIGH alert. Should be < red_threshold_minutes;
        the loader clamps inversion at load time.
    cooldown_minutes:
        Minimum gap between two fires for the same source for the
        same user. Prevents a flapping feed from filling the alert
        table — the next fire after a successful one is suppressed
        until this many minutes have elapsed.
    """
    enabled: bool = True
    red_threshold_minutes: int = 60
    yellow_threshold_minutes: int = 30
    cooldown_minutes: int = 120


# kv_state key prefixes. Per-user scoping is encoded in the key suffix —
# the legacy global bucket lives at ``…config:`` (empty suffix) so
# existing rows remain reachable after the first authenticated user
# saves their own config.
_CONFIG_KEY_PREFIX = "source_health_alert_config:"
_COOLDOWN_KEY_PREFIX = "source_alert_cooldown:"
_COUNTER_KEY_PREFIX = "source_alert_recent_fires:"


# ─────────────────────────────────────────────────────────────────────────────
#  User-id resolution + key helpers
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_user_id(user_id: Optional[str]) -> str:
    """Pick the user_id to scope config + cooldown to.

    Mirrors the resolution rule in ``alert_engine_v2._resolve_user_id``:
    explicit non-None ``user_id`` wins (so tests can pin a known id),
    otherwise consult the Streamlit session via ``current_user_id``
    (returns ``""`` outside Streamlit — the legacy global bucket).
    Never raises.
    """
    if user_id is None:
        try:
            from state.user_scope import current_user_id
            return current_user_id()
        except Exception:
            return ""
    if not isinstance(user_id, str):
        return ""
    return user_id


def _config_key(user_id: str) -> str:
    """kv_state row key for the config blob."""
    return f"{_CONFIG_KEY_PREFIX}{user_id}"


def _cooldown_key(user_id: str, source_id: str) -> str:
    """kv_state row key for a (user, source) cooldown timestamp."""
    return f"{_COOLDOWN_KEY_PREFIX}{user_id}:{source_id}"


def _counter_key(user_id: str) -> str:
    """kv_state row key for the rolling 'recent fires' counter blob."""
    return f"{_COUNTER_KEY_PREFIX}{user_id}"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat()


# ─────────────────────────────────────────────────────────────────────────────
#  Config load / save
# ─────────────────────────────────────────────────────────────────────────────

def load_config(*, user_id: Optional[str] = None) -> SourceHealthAlertConfig:
    """Read the config blob from kv_state — defaults when missing.

    Missing row, malformed JSON, or any DB read failure → defaults.
    Never raises. The yellow_threshold is clamped to be strictly less
    than the red_threshold on the way out so the comparison logic in
    ``check_source_health_and_fire`` can rely on the invariant without
    re-checking.
    """
    uid = _resolve_user_id(user_id)
    try:
        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM kv_state WHERE key = ?", (_config_key(uid),)
        ).fetchone()
        if row is None:
            return SourceHealthAlertConfig()
        raw = row["value"] if hasattr(row, "keys") else row[0]
        if not raw:
            return SourceHealthAlertConfig()
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return SourceHealthAlertConfig()
    except Exception as exc:
        logger.debug(
            f"source_health_alerts.load_config: read failed for "
            f"user_id={uid!r}: {exc}"
        )
        return SourceHealthAlertConfig()

    cfg = SourceHealthAlertConfig(
        enabled=bool(parsed.get("enabled", True)),
        red_threshold_minutes=_coerce_int(
            parsed.get("red_threshold_minutes"), 60
        ),
        yellow_threshold_minutes=_coerce_int(
            parsed.get("yellow_threshold_minutes"), 30
        ),
        cooldown_minutes=_coerce_int(parsed.get("cooldown_minutes"), 120),
    )

    # Clamp invariants. Negative thresholds make no sense — coerce to
    # the default. Yellow >= red would invert the meaning of the two
    # tiers — when callers mis-order the inputs we pin yellow to one
    # minute below red so a fire still goes through the right tier.
    if cfg.red_threshold_minutes < 0:
        cfg.red_threshold_minutes = 60
    if cfg.yellow_threshold_minutes < 0:
        cfg.yellow_threshold_minutes = 30
    if cfg.cooldown_minutes < 0:
        cfg.cooldown_minutes = 120
    if cfg.yellow_threshold_minutes >= cfg.red_threshold_minutes:
        cfg.yellow_threshold_minutes = max(0, cfg.red_threshold_minutes - 1)
    return cfg


def save_config(
    cfg: SourceHealthAlertConfig, *, user_id: Optional[str] = None
) -> bool:
    """Persist the config blob to kv_state. Returns success.

    Never raises — write errors are logged at debug level and the
    return value (False) tells the caller (typically the UI Save button)
    to surface a "couldn't save" message.
    """
    uid = _resolve_user_id(user_id)
    try:
        from state.db import get_connection

        payload = json.dumps(asdict(cfg))
        conn = get_connection()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_state (key, value, updated_at) "
                "VALUES (?, ?, ?)",
                (_config_key(uid), payload, _now_iso()),
            )
        return True
    except Exception as exc:
        logger.debug(
            f"source_health_alerts.save_config: write failed for "
            f"user_id={uid!r}: {exc}"
        )
        return False


def _coerce_int(value: Any, default: int) -> int:
    """Best-effort int coercion. Bad input → default."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ─────────────────────────────────────────────────────────────────────────────
#  Cooldown helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_source_alert_cooldown(
    source_id: str, *, user_id: Optional[str] = None
) -> Optional[str]:
    """Return the ISO timestamp of the last fire for ``(user, source)``,
    or ``None`` if no prior fire exists.

    Never raises — a DB read failure returns ``None`` (the safe default
    that lets the loop re-fire, on the principle that missing
    cooldown data should not silence a real outage).
    """
    uid = _resolve_user_id(user_id)
    try:
        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM kv_state WHERE key = ?",
            (_cooldown_key(uid, str(source_id or "")),),
        ).fetchone()
        if row is None:
            return None
        raw = row["value"] if hasattr(row, "keys") else row[0]
        if not raw:
            return None
        return str(raw)
    except Exception as exc:
        logger.debug(
            f"source_health_alerts.get_source_alert_cooldown: read failed "
            f"for user_id={uid!r} source={source_id!r}: {exc}"
        )
        return None


def set_source_alert_cooldown(
    source_id: str, *, user_id: Optional[str] = None
) -> None:
    """Mark ``(user, source)`` as having just fired. Stamps now-ISO into
    kv_state under the cooldown key.

    Best-effort — a write failure is logged at debug. Callers in the
    hot path do not need to react: the next pass will simply re-fire
    (worst case: one duplicate alert).
    """
    uid = _resolve_user_id(user_id)
    try:
        from state.db import get_connection

        now = _now_iso()
        conn = get_connection()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_state (key, value, updated_at) "
                "VALUES (?, ?, ?)",
                (_cooldown_key(uid, str(source_id or "")), now, now),
            )
    except Exception as exc:
        logger.debug(
            f"source_health_alerts.set_source_alert_cooldown: write failed "
            f"for user_id={uid!r} source={source_id!r}: {exc}"
        )


def _within_cooldown(
    source_id: str, cooldown_minutes: int, *, user_id: Optional[str] = None
) -> bool:
    """True iff a prior fire for ``(user, source)`` was within the window."""
    if cooldown_minutes <= 0:
        return False
    last = get_source_alert_cooldown(source_id, user_id=user_id)
    if last is None:
        return False
    try:
        last_dt = datetime.fromisoformat(last)
    except (TypeError, ValueError):
        return False
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    return (_now_utc() - last_dt) < timedelta(minutes=cooldown_minutes)


# ─────────────────────────────────────────────────────────────────────────────
#  Recent-fires counter (cheap rolling tally for the UI status line)
# ─────────────────────────────────────────────────────────────────────────────

def _load_recent_fires(user_id: str) -> list[str]:
    """Read the rolling ISO-timestamp list of recent fires.

    Returns an empty list on missing row / parse failure / DB error.
    """
    try:
        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM kv_state WHERE key = ?", (_counter_key(user_id),)
        ).fetchone()
        if row is None:
            return []
        raw = row["value"] if hasattr(row, "keys") else row[0]
        if not raw:
            return []
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            return []
        return [str(x) for x in parsed if isinstance(x, str)]
    except Exception:
        return []


def _save_recent_fires(user_id: str, fires: list[str]) -> None:
    """Persist the rolling ISO-timestamp list. Best-effort."""
    try:
        from state.db import get_connection

        now = _now_iso()
        conn = get_connection()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_state (key, value, updated_at) "
                "VALUES (?, ?, ?)",
                (_counter_key(user_id), json.dumps(fires), now),
            )
    except Exception:
        # Counter is a nice-to-have for the UI. A write failure must
        # not interrupt the orchestrator.
        pass


def _record_recent_fire(user_id: str, when_iso: str) -> None:
    """Append a fresh fire timestamp + trim to entries within the last
    hour. The UI uses this to render a 'last fired N alerts in the last
    hour' status line."""
    fires = _load_recent_fires(user_id)
    fires.append(when_iso)
    cutoff = _now_utc() - timedelta(hours=1)
    trimmed: list[str] = []
    for ts in fires:
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                trimmed.append(ts)
        except (TypeError, ValueError):
            continue
    _save_recent_fires(user_id, trimmed)


def get_recent_fire_count(*, user_id: Optional[str] = None) -> int:
    """How many alerts has the auto-alerter fired in the last hour for
    this user? Used by the UI status line and the CLI status command."""
    uid = _resolve_user_id(user_id)
    fires = _load_recent_fires(uid)
    cutoff = _now_utc() - timedelta(hours=1)
    count = 0
    for ts in fires:
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                count += 1
        except (TypeError, ValueError):
            continue
    return count


# ─────────────────────────────────────────────────────────────────────────────
#  Status classification
# ─────────────────────────────────────────────────────────────────────────────

def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp; treat naive as UTC. None on failure."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _classify_source(
    stats: dict, cfg: SourceHealthAlertConfig
) -> Optional[str]:
    """Decide whether a source needs an alert and at what severity.

    Returns ``'CRITICAL'`` (fire as CRITICAL), ``'HIGH'`` (fire as
    HIGH), or ``None`` (no alert — source is healthy enough).

    Decision rules:
    * last_status == 'up' AND last_ping is fresh enough → None
      (yellow_threshold suppresses an alert for fresh-up sources).
    * last_status == 'down' → CRITICAL (down is always red, regardless
      of how recently it happened; a freshly-failed feed is still
      broken).
    * last_started_at older than red_threshold_minutes → CRITICAL
      (stale beyond the red threshold — even 'up' counts, since we
      have no way to know whether the feed is still alive).
    * last_status == 'degraded' OR last_started_at older than
      yellow_threshold_minutes → HIGH.
    * Otherwise → None.
    """
    if not isinstance(stats, dict):
        return None
    status = str(stats.get("last_status", "") or "").lower()
    last_started_at = stats.get("last_started_at")
    last_dt = _parse_iso(last_started_at)

    # 'down' is always CRITICAL — no need to check staleness.
    if status == "down":
        return "CRITICAL"

    # Compute staleness (minutes since the last ping) if we have one.
    stale_minutes: Optional[float] = None
    if last_dt is not None:
        delta = _now_utc() - last_dt
        stale_minutes = delta.total_seconds() / 60.0

    # Stale beyond the red threshold escalates to CRITICAL even for
    # 'up' sources — the last ping is too old to be evidence of
    # current health.
    if stale_minutes is not None and stale_minutes >= cfg.red_threshold_minutes:
        return "CRITICAL"

    # 'degraded' is HIGH unconditionally — the probe came back but
    # the payload was wrong-shaped or empty. That's a real degradation.
    if status == "degraded":
        return "HIGH"

    # Yellow: stale beyond yellow but inside red.
    if (
        stale_minutes is not None
        and stale_minutes >= cfg.yellow_threshold_minutes
    ):
        return "HIGH"

    return None


def _build_alert(
    source_id: str, stats: dict, severity: str
):
    """Build a ShippingAlert for a degraded source.

    Lazy import of ``ShippingAlert`` keeps this module light for
    callers that only want the config dataclass.
    """
    from engine.alert_engine_v2 import ShippingAlert, _new_id, _now_iso as alerts_now_iso

    last_status = str(stats.get("last_status", "") or "unknown")
    last_at = str(stats.get("last_started_at", "") or "n/a")
    down_count = int(stats.get("down_count", 0) or 0)
    degraded_count = int(stats.get("degraded_count", 0) or 0)
    total_count = int(stats.get("count", 0) or 0)

    title = f"Source {source_id} is {severity.lower()}"
    body = (
        f"Data feed '{source_id}' last ping was {last_status} at {last_at}. "
        f"In the last 24h: {down_count} down / {degraded_count} degraded "
        f"out of {total_count} pings. Auto-alert tier: {severity}."
    )
    return ShippingAlert(
        alert_id=_new_id(),
        created_at=alerts_now_iso(),
        alert_type="SOURCE_HEALTH",
        severity=severity,
        title=title,
        body=body,
        ticker="",
        route_id="",
        port_locode=str(source_id or "")[:32],  # piggy-back as the entity key for dedup
        value=float(down_count),
        threshold=0.0,
        change_pct=0.0,
        acknowledged=False,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Orchestrator
# ─────────────────────────────────────────────────────────────────────────────

def check_source_health_and_fire(
    config: Optional[SourceHealthAlertConfig] = None,
    *,
    user_id: Optional[str] = None,
) -> dict:
    """Walk current source-health snapshot and fire alerts for degraded sources.

    Steps:
      1. Resolve ``user_id`` (explicit param > current Streamlit user >
         empty/legacy bucket).
      2. Load config from kv_state if not supplied. If disabled,
         return zero counts immediately.
      3. Call ``engine.source_health.get_health_summary()`` to read the
         current per-source latest-ping snapshot.
      4. For each source:
         * Skip if within cooldown (counts as ``skipped_cooldown``).
         * Classify status into CRITICAL / HIGH / None via the
           thresholds in config.
         * If a severity is needed, build a ShippingAlert and persist
           via ``save_alerts``. Mark cooldown + recent-fire counter on
           success.
         * Any per-source exception increments ``errored`` and does
           NOT break the loop.
      5. Return ``{"fired": N, "skipped_cooldown": N, "errored": N}``.

    NEVER raises at the top level — every exception (config load,
    summary fetch, per-source classify, save) is captured. A summary
    fetch that raises returns ``{"fired": 0, "skipped_cooldown": 0,
    "errored": 1}`` so the caller sees the failure in the counters.
    """
    counts = {"fired": 0, "skipped_cooldown": 0, "errored": 0}
    uid = _resolve_user_id(user_id)

    try:
        cfg = config if config is not None else load_config(user_id=uid)
        if not cfg.enabled:
            return counts
    except Exception as exc:
        logger.debug(
            f"source_health_alerts.check_source_health_and_fire: "
            f"config load failed: {exc}"
        )
        counts["errored"] += 1
        return counts

    try:
        from engine.source_health import get_health_summary
        summary = get_health_summary(window_hours=24)
    except Exception as exc:
        logger.debug(
            f"source_health_alerts.check_source_health_and_fire: "
            f"get_health_summary failed: {exc}"
        )
        counts["errored"] += 1
        return counts

    by_source = summary.get("by_source", {}) if isinstance(summary, dict) else {}
    if not isinstance(by_source, dict):
        return counts

    for source_id, stats in by_source.items():
        try:
            # Cooldown gate — even if the source is screaming red, the
            # operator already got an alert in the last `cooldown_minutes`
            # so skip.
            if _within_cooldown(
                str(source_id), cfg.cooldown_minutes, user_id=uid
            ):
                counts["skipped_cooldown"] += 1
                continue

            severity = _classify_source(stats if isinstance(stats, dict) else {}, cfg)
            if severity is None:
                continue

            alert = _build_alert(str(source_id), stats, severity)

            # Lazy import save_alerts here so a broken
            # engine.alert_engine_v2 import doesn't kill the WHOLE
            # loop — the per-source try/except catches and counts it.
            from engine.alert_engine_v2 import save_alerts

            save_alerts([alert], user_id=uid)

            # Only stamp cooldown + counter AFTER a successful save.
            # If save_alerts raised, the next pass will retry the
            # alert; the operator is not left blind.
            set_source_alert_cooldown(str(source_id), user_id=uid)
            _record_recent_fire(uid, _now_iso())
            counts["fired"] += 1
        except Exception as exc:
            logger.debug(
                f"source_health_alerts.check_source_health_and_fire: "
                f"source={source_id!r} failed: {exc}"
            )
            counts["errored"] += 1
            continue

    return counts
