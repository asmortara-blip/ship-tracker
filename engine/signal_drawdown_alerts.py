"""Signal-tier drawdown kill-switch alerter (B2, on R004's signal ledger).

When a conviction tier's realized, look-ahead-free track record craters —
either its hit-rate falls below a floor or its peak-to-now drawdown blows past
a threshold — :func:`state.signal_ledger.tier_drawdown` flags it ``STAND_DOWN``.
This module turns that flag into a ``SIGNAL_DRAWDOWN`` ShippingAlert so the
operator is told to demote the tier instead of discovering it after the fact.

It mirrors ``engine.perf_budgets.check_and_alert`` exactly:

* One alert per STAND_DOWN tier, gated by a per-tier cooldown so a chronically
  underwater tier does not carpet-bomb the alert feed.
* The cooldown row lives in the existing ``kv_state`` table under
  ``signal_drawdown_cooldown:<user_id>:<tier>`` carrying the ISO timestamp of
  the last successful fire; the suppression window is ``cooldown_hours``.
* The tier label rides in ``port_locode`` as the dedup entity key — the same
  trick ``perf_budgets`` uses for ``tab_module`` and ``source_health_alerts``
  uses for the source id — so two near-simultaneous fires for the same tier
  collapse to one row via ``save_alerts``' standard dedup.

It does NOT bump SCHEMA_VERSION (cooldowns ride the existing kv_state table)
and it NEVER raises at the top level — a check failure must never block any
sibling scheduler job.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from loguru import logger

_COOLDOWN_KEY_PREFIX = "signal_drawdown_cooldown:"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat()


def _resolve_user_id(user_id: Optional[str]) -> str:
    """Pick the user_id to scope cooldown to.

    Mirrors ``perf_budgets._resolve_user_id``: an explicit non-None ``user_id``
    wins (so tests + the worker can pin a known id), otherwise consult the
    Streamlit session via ``current_user_id`` (returns ``""`` outside Streamlit
    — the legacy global bucket). Never raises.
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


def _cooldown_key(user_id: str, tier: str) -> str:
    """kv_state row key for a (user, tier) cooldown timestamp."""
    return f"{_COOLDOWN_KEY_PREFIX}{user_id}:{tier}"


def _get_cooldown(user_id: str, tier: str) -> Optional[str]:
    """Return the ISO timestamp of the last fire for (user, tier) or None."""
    try:
        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM kv_state WHERE key = ?",
            (_cooldown_key(user_id, tier),),
        ).fetchone()
        if row is None:
            return None
        raw = row["value"] if hasattr(row, "keys") else row[0]
        return str(raw) if raw else None
    except Exception as exc:
        logger.debug(
            f"signal_drawdown_alerts._get_cooldown: read failed for "
            f"user_id={user_id!r} tier={tier!r}: {exc}"
        )
        return None


def _set_cooldown(
    user_id: str, tier: str, *, now: Optional[datetime] = None
) -> None:
    """Stamp now-ISO into the cooldown row for (user, tier). Best-effort.

    ``now`` lets the caller pin one consistent clock across the read+write of a
    single check pass (so the cooldown is stamped at the same instant it was
    evaluated against); defaults to real wall-clock.
    """
    try:
        from state.db import get_connection

        now = (now or _now_utc()).isoformat()
        conn = get_connection()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_state (key, value, updated_at) "
                "VALUES (?, ?, ?)",
                (_cooldown_key(user_id, tier), now, now),
            )
    except Exception as exc:
        logger.debug(
            f"signal_drawdown_alerts._set_cooldown: write failed for "
            f"user_id={user_id!r} tier={tier!r}: {exc}"
        )


def _within_cooldown(
    user_id: str,
    tier: str,
    cooldown_hours: int,
    *,
    now: Optional[datetime] = None,
) -> bool:
    """True iff a prior fire for (user, tier) was within ``cooldown_hours``."""
    if cooldown_hours <= 0:
        return False
    last = _get_cooldown(user_id, tier)
    if last is None:
        return False
    try:
        last_dt = datetime.fromisoformat(last)
    except (TypeError, ValueError):
        return False
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    current = now if now is not None else _now_utc()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return (current - last_dt) < timedelta(hours=cooldown_hours)


def _trigger_reasons(
    info: dict, *, hit_floor: float, dd_threshold_pct: float
) -> list[str]:
    """Which of the two kill-switch conditions tripped (one or both)."""
    reasons: list[str] = []
    if float(info.get("hit_rate", 0.0)) < hit_floor:
        reasons.append(
            f"hit-rate {float(info.get('hit_rate', 0.0)):.0%} below "
            f"{hit_floor:.0%} floor"
        )
    if float(info.get("current_drawdown_pct", 0.0)) > dd_threshold_pct:
        reasons.append(
            f"drawdown {float(info.get('current_drawdown_pct', 0.0)):.1f}% "
            f"beyond {dd_threshold_pct:.0f}% limit"
        )
    return reasons


def _build_alert(
    tier: str, info: dict, *, hit_floor: float, dd_threshold_pct: float
):
    """Build a HIGH ``SIGNAL_DRAWDOWN`` ShippingAlert for a STAND_DOWN tier.

    Severity is HIGH (CRITICAL stays reserved for source-health red, per the
    perf-budget convention). ``value`` carries the current drawdown and
    ``threshold`` the limit; the tier label rides in ``port_locode`` as the
    dedup entity key. The body names which condition(s) tripped so the operator
    can triage without opening the ledger.
    """
    from engine.alert_engine_v2 import (
        ShippingAlert,
        _new_id,
        _now_iso as alerts_now_iso,
    )

    n = int(info.get("n", 0))
    hit = float(info.get("hit_rate", 0.0))
    mean_pct = float(info.get("mean_signed_return_pct", 0.0))
    cur_dd = float(info.get("current_drawdown_pct", 0.0))
    max_dd = float(info.get("max_drawdown_pct", 0.0))
    reasons = _trigger_reasons(
        info, hit_floor=hit_floor, dd_threshold_pct=dd_threshold_pct
    )
    why = " and ".join(reasons) if reasons else "kill-switch threshold crossed"

    title = f"STAND DOWN: '{tier}'-conviction tier drawdown kill-switch"
    body = (
        f"The '{tier}' conviction tier tripped the drawdown kill-switch — "
        f"{why}. Over {n} realized (look-ahead-free) signals it now sits at "
        f"{cur_dd:.1f}% current drawdown (max {max_dd:.1f}%), {hit:.0%} hit-rate, "
        f"{mean_pct:+.1f}% mean signed return. Demote or pause new ideas at this "
        f"conviction until the track record recovers. (Forward marks on the "
        f"frozen signal ledger; nothing refit.)"
    )
    return ShippingAlert(
        alert_id=_new_id(),
        created_at=alerts_now_iso(),
        alert_type="SIGNAL_DRAWDOWN",
        severity="HIGH",
        title=title,
        body=body,
        ticker="",
        route_id="",
        # Tier label piggybacks on port_locode as the dedup entity key —
        # truncated to the column width like perf_budgets/source_health do.
        port_locode=str(tier or "")[:32],
        value=cur_dd,
        threshold=float(dd_threshold_pct),
        change_pct=0.0,
        acknowledged=False,
    )


def check_and_alert_drawdown(
    stock_data: Any,
    *,
    user_id: Optional[str] = None,
    min_n: int = 5,
    hit_floor: float = 0.40,
    dd_threshold_pct: float = 15.0,
    cooldown_hours: int = 24,
    now: Optional[datetime] = None,
) -> dict:
    """Mark the signal ledger forward, then fire one alert per STAND_DOWN tier.

    Returns a count dict shaped like the perf-budget + source-health alerters
    so the CLI and worker can log a one-line summary::

        {"checked": N, "stand_down": N, "alerted": N, "skipped_cooldown": N}

    Per-tier try/except inside the loop — a save_alerts failure for one tier
    must not prevent the rest from firing. Cooldown is stamped ONLY after a
    successful save (a save that raises does NOT mark the cooldown, so the next
    pass retries the alert). NEVER raises at the top level.
    """
    counts = {"checked": 0, "stand_down": 0, "alerted": 0, "skipped_cooldown": 0}
    uid = _resolve_user_id(user_id)

    try:
        from state.signal_ledger import tier_drawdown

        tiers = tier_drawdown(
            stock_data,
            min_n=min_n,
            hit_floor=hit_floor,
            dd_threshold_pct=dd_threshold_pct,
        )
    except Exception as exc:
        logger.debug(
            f"signal_drawdown_alerts.check_and_alert_drawdown: "
            f"tier_drawdown failed: {exc}"
        )
        return counts

    counts["checked"] = len(tiers)
    stand_down = {t: info for t, info in tiers.items()
                  if info.get("status") == "STAND_DOWN"}
    counts["stand_down"] = len(stand_down)
    if not stand_down:
        return counts

    for tier, info in stand_down.items():
        try:
            if _within_cooldown(uid, tier, cooldown_hours, now=now):
                counts["skipped_cooldown"] += 1
                continue

            alert = _build_alert(
                tier, info, hit_floor=hit_floor, dd_threshold_pct=dd_threshold_pct
            )

            # Lazy import so a broken alert_engine_v2 doesn't kill the whole
            # loop — the per-tier try/except catches and counts it.
            from engine.alert_engine_v2 import save_alerts

            save_alerts([alert], user_id=uid)

            # Only stamp cooldown AFTER a successful save, on the same clock the
            # cooldown was just evaluated against.
            _set_cooldown(uid, tier, now=now)
            counts["alerted"] += 1
        except Exception as exc:
            logger.debug(
                f"signal_drawdown_alerts.check_and_alert_drawdown: "
                f"per-tier tier={tier!r} failed: {exc}"
            )
            continue

    return counts
