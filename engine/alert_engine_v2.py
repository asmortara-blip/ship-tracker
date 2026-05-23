"""Comprehensive shipping alert engine v2 — detection, persistence, and acknowledgement."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from loguru import logger


# ─────────────────────────────────────────────────────────────────────────────
#  Dedup window
# ─────────────────────────────────────────────────────────────────────────────

# Default time window for collapsing repeat alert fires of the same
# dedup_key into one row (incrementing fire_count instead of inserting
# a new row). Sixty minutes is the right ballpark for a flaky data
# feed that bounces a value across its threshold a few times an hour —
# long enough to absorb realistic feed jitter, short enough that a
# genuinely re-triggered alert the next morning shows up as a new row.
# Tests monkeypatch this via ``monkeypatch.setattr(engv2,
# "_DEDUP_WINDOW_MINUTES", N)`` to exercise the boundary.
_DEDUP_WINDOW_MINUTES: int = 60


# ─────────────────────────────────────────────────────────────────────────────
#  Dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ShippingAlert:
    alert_id: str           # UUID
    created_at: str         # ISO timestamp
    alert_type: str         # BDI_MOVE | SIGNAL_FIRE | CONGESTION | RATE_SURGE | STOCK_MOVE | MACRO
    severity: str           # CRITICAL | HIGH | MEDIUM | LOW
    title: str
    body: str               # 2-3 sentence description
    ticker: str             # if stock-related, else ""
    route_id: str           # if freight-related, else ""
    port_locode: str        # if port-related, else ""
    value: float            # the triggering value
    threshold: float        # the threshold that was crossed
    change_pct: float       # % change that triggered
    acknowledged: bool      # has user seen it


@dataclass
class AlertRule:
    rule_id: str
    name: str
    alert_type: str
    enabled: bool
    threshold: float        # e.g. 5.0 for 5% BDI move
    severity: str
    # Optional rule → channel routing. Empty list (default + legacy
    # behaviour) means alerts from this rule are eligible for every
    # delivery channel whose severity threshold matches. Non-empty
    # means only channels whose ``name`` appears in this list are
    # eligible. Matching is by channel NAME, not channel_id, so users
    # editing rules in the UI work with the same labels they see on
    # the channels page.
    target_channels: list[str] = field(default_factory=list)
    # Per-rule cooldown in minutes (v18). 0 (the default) preserves
    # the legacy "fire on every evaluation that trips the rule"
    # behaviour — existing rules without the field load as 0 and act
    # exactly as they did before. A positive value N suppresses
    # repeat fires of the SAME rule_id (for the SAME user) for N
    # minutes after a successful fire. Cooldown is orthogonal to the
    # v14 dedup_key collapse — dedup merges bounces of an alert that
    # already fired; cooldown stops the rule from firing in the
    # first place.
    cooldown_minutes: int = 0


# ─────────────────────────────────────────────────────────────────────────────
#  Severity ordering
# ─────────────────────────────────────────────────────────────────────────────

_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


# ─────────────────────────────────────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


def _dedup_key(alert: "ShippingAlert") -> str:
    """Build a stable string key identifying "the same alert" for dedup
    purposes.

    The key is the five-tuple (``alert_type``, ``severity``, ``ticker``,
    ``route_id``, ``port_locode``) joined by ``|`` after escaping any
    literal ``|`` characters in the source fields. Two alerts share a
    dedup_key iff they are reporting the same condition on the same
    entity at the same severity — e.g. two BDI_MOVE alerts at HIGH
    severity collide; a BDI_MOVE at HIGH and a BDI_MOVE at CRITICAL on
    the same data do not (severity escalation should surface as a new
    row, not a fire_count bump on the prior HIGH row).

    The pipe escape exists so a hypothetical port_locode like
    ``A|B`` cannot smuggle a separator into the key and collide with
    a different (route_id="A", port_locode="B") shape. The
    ``backslash → \\\\`` escape goes first so the pipe escape's
    backslash is not itself re-escaped on the second pass.
    """
    def _esc(s: str) -> str:
        # Order matters: escape backslash FIRST so the pipe-escape's
        # introduced backslash is not re-escaped on the second pass.
        return (s or "").replace("\\", "\\\\").replace("|", "\\|")

    return "|".join((
        _esc(alert.alert_type),
        _esc(alert.severity),
        _esc(alert.ticker),
        _esc(alert.route_id),
        _esc(alert.port_locode),
    ))


def _make(
    alert_type: str,
    severity: str,
    title: str,
    body: str,
    *,
    ticker: str = "",
    route_id: str = "",
    port_locode: str = "",
    value: float = 0.0,
    threshold: float = 0.0,
    change_pct: float = 0.0,
) -> ShippingAlert:
    return ShippingAlert(
        alert_id=_new_id(),
        created_at=_now_iso(),
        alert_type=alert_type,
        severity=severity,
        title=title,
        body=body,
        ticker=ticker,
        route_id=route_id,
        port_locode=port_locode,
        value=value,
        threshold=threshold,
        change_pct=change_pct,
        acknowledged=False,
    )


def _bdi_series(macro_data: dict):
    """Return a sorted pandas Series of BDI values, or None.

    Tries multiple key conventions: the real FRED series ID `BSXRLM` first
    (which is what `data/macro_data.py` actually fetches), then the legacy
    aliases BDIY / BDI / bdi that older callers may still pass. Without the
    BSXRLM check the alert engine silently misses every BDI alert when fed
    macro_data straight from the FRED loader.
    """
    try:
        import pandas as pd
        # Avoid `a or b` on DataFrames (DataFrame.__bool__ raises).
        bdi_df = macro_data.get("BSXRLM")
        if bdi_df is None:
            bdi_df = macro_data.get("BDIY")
        if bdi_df is None:
            bdi_df = macro_data.get("BDI")
        if bdi_df is None:
            bdi_df = macro_data.get("bdi")
        if bdi_df is None or getattr(bdi_df, "empty", True):
            return None
        date_col = "date" if "date" in bdi_df.columns else bdi_df.columns[0]
        val_col = (
            "value" if "value" in bdi_df.columns
            else [c for c in bdi_df.columns if c != date_col][0]
        )
        sorted_df = bdi_df.sort_values(date_col)
        return sorted_df[val_col].dropna().reset_index(drop=True)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  Alert detection functions
# ─────────────────────────────────────────────────────────────────────────────

def check_bdi_alerts(macro_data: dict, threshold_pct: float = 5.0) -> list[ShippingAlert]:
    """Fire if BDI moves >threshold_pct in 1 day or >10% in 7 days."""
    alerts: list[ShippingAlert] = []
    series = _bdi_series(macro_data)
    if series is None or len(series) < 2:
        return alerts

    current = float(series.iloc[-1])

    # 1-day move
    prev_1d = float(series.iloc[-2])
    if prev_1d != 0:
        chg_1d = (current - prev_1d) / prev_1d * 100.0
        if abs(chg_1d) >= threshold_pct:
            direction = "surged" if chg_1d > 0 else "dropped"
            severity = "CRITICAL" if abs(chg_1d) >= threshold_pct * 2 else "HIGH"
            alerts.append(_make(
                alert_type="BDI_MOVE",
                severity=severity,
                title=f"BDI {direction.title()} {abs(chg_1d):.1f}% in 1 Day",
                body=(
                    f"The Baltic Dry Index has {direction} {abs(chg_1d):.1f}% in a single session, "
                    f"reaching {current:,.0f} points against the {threshold_pct:.0f}% single-day threshold. "
                    f"This signals a sharp shift in dry bulk shipping demand."
                ),
                value=current,
                threshold=threshold_pct,
                change_pct=chg_1d,
            ))

    # 7-day move (>10%)
    if len(series) >= 8:
        prev_7d = float(series.iloc[-8])
        if prev_7d != 0:
            chg_7d = (current - prev_7d) / prev_7d * 100.0
            if abs(chg_7d) >= 10.0:
                direction = "climbed" if chg_7d > 0 else "fallen"
                severity = "CRITICAL" if abs(chg_7d) >= 20.0 else "HIGH"
                alerts.append(_make(
                    alert_type="BDI_MOVE",
                    severity=severity,
                    title=f"BDI {direction.title()} {abs(chg_7d):.1f}% Over 7 Days",
                    body=(
                        f"The Baltic Dry Index has {direction} {abs(chg_7d):.1f}% over the past week, "
                        f"reaching {current:,.0f} points, breaching the 10% weekly move threshold. "
                        f"Sustained momentum of this magnitude typically precedes repricing across container routes."
                    ),
                    value=current,
                    threshold=10.0,
                    change_pct=chg_7d,
                ))

    return alerts


def check_signal_alerts(signals: list) -> list[ShippingAlert]:
    """Fire for every new HIGH conviction signal."""
    alerts: list[ShippingAlert] = []
    for sig in (signals or []):
        conviction = getattr(sig, "conviction", None)
        if conviction != "HIGH":
            continue
        ticker = getattr(sig, "ticker", "")
        signal_name = getattr(sig, "signal_name", "High Conviction Signal")
        direction = getattr(sig, "direction", "LONG")
        strength = getattr(sig, "strength", 0.0)
        exp_ret = getattr(sig, "expected_return_pct", 0.0)
        horizon = getattr(sig, "time_horizon", "")
        rationale = getattr(sig, "rationale", "")
        severity = "HIGH" if strength >= 0.8 else "MEDIUM"
        alerts.append(_make(
            alert_type="SIGNAL_FIRE",
            severity=severity,
            title=f"High Conviction Signal: {ticker} {direction} — {signal_name}",
            body=(
                f"{ticker} has generated a HIGH conviction {direction} signal ({signal_name}) "
                f"with {strength:.0%} strength and {exp_ret:+.1f}% expected return over {horizon}. "
                f"{rationale[:180] + '...' if len(rationale) > 180 else rationale}"
            ),
            ticker=ticker,
            value=strength,
            threshold=0.0,
            change_pct=exp_ret,
        ))
    return alerts


def check_congestion_alerts(port_results: list, threshold: float = 0.75) -> list[ShippingAlert]:
    """Fire if any port congestion score exceeds threshold."""
    alerts: list[ShippingAlert] = []
    for port in (port_results or []):
        score = getattr(port, "congestion_score", None)
        if score is None or score <= threshold:
            continue
        locode = getattr(port, "locode", getattr(port, "port_id", ""))
        name = getattr(port, "name", getattr(port, "port_name", locode))
        excess = (score - threshold) / threshold * 100.0
        severity = "CRITICAL" if score >= 0.90 else ("HIGH" if score >= 0.82 else "MEDIUM")
        alerts.append(_make(
            alert_type="CONGESTION",
            severity=severity,
            title=f"Port Congestion Alert: {name} ({locode})",
            body=(
                f"{name} ({locode}) is reporting a congestion score of {score:.0%}, "
                f"exceeding the {threshold:.0%} alert threshold by {excess:.1f}%. "
                f"Expect elevated dwell times, increased port fees, and potential vessel bunching."
            ),
            port_locode=locode,
            value=score,
            threshold=threshold,
            change_pct=excess,
        ))
    return alerts


def check_rate_alerts(freight_data: dict, threshold_pct: float = 8.0) -> list[ShippingAlert]:
    """Fire if any freight rate moves >threshold_pct in 7 days.

    Per-route overrides: if ``engine.route_thresholds.load_route_thresholds()``
    returns an entry for a given route_id, that entry's ``threshold_pct``
    replaces the function-level default for that route only, and its
    ``severity`` tier overrides the auto-detected CRITICAL/HIGH that
    would normally be picked from move magnitude. Routes WITHOUT an
    override behave exactly as they did before — the override path is
    backward compatible when no overrides exist (empty dict).
    """
    alerts: list[ShippingAlert] = []
    try:
        import pandas as pd
    except ImportError:
        return alerts

    # One SELECT for the whole route set — cheap, and keeps the inner
    # loop free of repeated SQLite hits. Failures inside the helper
    # return an empty dict so the loop falls through to the function-
    # level default for every route.
    try:
        from engine.route_thresholds import load_route_thresholds
        route_overrides = load_route_thresholds()
    except Exception as exc:
        logger.debug(f"check_rate_alerts: override load failed: {exc}")
        route_overrides = {}

    for route_id, df in (freight_data or {}).items():
        if not isinstance(df, pd.DataFrame) or df.empty:
            continue
        rate_col = next(
            (c for c in ("rate_usd_per_feu", "rate", "value") if c in df.columns),
            None,
        )
        if rate_col is None:
            continue
        date_col = "date" if "date" in df.columns else None
        sorted_df = df.sort_values(date_col) if date_col else df
        vals = sorted_df[rate_col].dropna()
        if len(vals) < 2:
            continue

        current = float(vals.iloc[-1])
        ref_idx = -8 if len(vals) >= 8 else 0
        ref = float(vals.iloc[ref_idx])
        if ref == 0:
            continue

        chg = (current - ref) / ref * 100.0

        # Resolve the effective threshold + severity for this route. The
        # function parameter remains the default; an override only flips
        # the behaviour for routes the user has explicitly configured.
        override = route_overrides.get(str(route_id))
        if override is not None:
            effective_threshold = override.threshold_pct
            # Pinned severity from the override — applied regardless of
            # move magnitude. This is the whole point of the severity
            # field: let the user say "this lane is critical to my
            # book; if it moves at all past my threshold, treat it as
            # CRITICAL" without me chasing the 2× rule.
            effective_severity = override.severity
        else:
            effective_threshold = threshold_pct
            # Legacy magnitude-driven severity: CRITICAL at ≥ 2× threshold,
            # HIGH otherwise. Unchanged from the original implementation.
            effective_severity = (
                "CRITICAL" if abs(chg) >= effective_threshold * 2 else "HIGH"
            )

        if abs(chg) < effective_threshold:
            continue

        direction = "surged" if chg > 0 else "collapsed"
        label = str(route_id).replace("_", " ").title()
        alerts.append(_make(
            alert_type="RATE_SURGE",
            severity=effective_severity,
            title=f"Rate {direction.title()}: {label} ({chg:+.1f}% / 7d)",
            body=(
                f"Freight rates on {label} have {direction} {abs(chg):.1f}% over the past 7 days, "
                f"reaching ${current:,.0f}/FEU against the {effective_threshold:.0f}% threshold. "
                f"{'Consider booking forward capacity before further escalation.' if chg > 0 else 'Spot market opportunity — delay forward bookings if possible.'}"
            ),
            route_id=str(route_id),
            value=current,
            threshold=effective_threshold,
            change_pct=chg,
        ))
    return alerts


def check_stock_alerts(stock_data: dict, threshold_pct: float = 8.0) -> list[ShippingAlert]:
    """Fire if ZIM/MATX/SBLK/DAC/CMRE moves >threshold_pct in 1 day."""
    alerts: list[ShippingAlert] = []
    watch = {"ZIM", "MATX", "SBLK", "DAC", "CMRE"}

    for ticker, df in (stock_data or {}).items():
        if ticker not in watch:
            continue
        try:
            import pandas as pd
            if not isinstance(df, pd.DataFrame) or df.empty or "close" not in df.columns:
                continue
            vals = df["close"].dropna()
            if len(vals) < 2:
                continue
            current = float(vals.iloc[-1])
            prev = float(vals.iloc[-2])
            if prev == 0:
                continue
            chg = (current - prev) / prev * 100.0
            if abs(chg) < threshold_pct:
                continue
            direction = "rallied" if chg > 0 else "sold off"
            severity = "CRITICAL" if abs(chg) >= threshold_pct * 1.75 else "HIGH"
            alerts.append(_make(
                alert_type="STOCK_MOVE",
                severity=severity,
                title=f"{ticker} {direction.title()} {abs(chg):.1f}% in 1 Day",
                body=(
                    f"{ticker} has {direction} {abs(chg):.1f}% in a single session, "
                    f"closing at ${current:.2f} vs the prior ${prev:.2f} close. "
                    f"This move exceeds the {threshold_pct:.0f}% single-day threshold and may signal a broader shipping equity shift."
                ),
                ticker=ticker,
                value=current,
                threshold=threshold_pct,
                change_pct=chg,
            ))
        except Exception:
            continue
    return alerts


def run_all_checks(
    port_results,
    route_results,
    insights,
    freight_data,
    macro_data,
    stock_data,
    *,
    bdi_threshold: float = 5.0,
    rate_threshold: float = 8.0,
    stock_threshold: float = 8.0,
    congestion_threshold: float = 0.75,
) -> list[ShippingAlert]:
    """Run all alert checks, return sorted by severity then created_at."""
    all_alerts: list[ShippingAlert] = []

    try:
        all_alerts.extend(check_bdi_alerts(macro_data or {}, bdi_threshold))
    except Exception as exc:
        logger.warning(f"BDI alert check failed: {exc}")

    try:
        all_alerts.extend(check_signal_alerts(insights or []))
    except Exception as exc:
        logger.warning(f"Signal alert check failed: {exc}")

    try:
        all_alerts.extend(check_congestion_alerts(port_results or [], congestion_threshold))
    except Exception as exc:
        logger.warning(f"Congestion alert check failed: {exc}")

    try:
        all_alerts.extend(check_rate_alerts(freight_data or {}, rate_threshold))
    except Exception as exc:
        logger.warning(f"Rate alert check failed: {exc}")

    try:
        all_alerts.extend(check_stock_alerts(stock_data or {}, stock_threshold))
    except Exception as exc:
        logger.warning(f"Stock alert check failed: {exc}")

    all_alerts.sort(key=lambda a: (
        _SEVERITY_ORDER.get(a.severity, 99),
        a.created_at,
    ))
    return all_alerts


# ─────────────────────────────────────────────────────────────────────────────
#  Persistence (SQLite-backed via state.db)
# ─────────────────────────────────────────────────────────────────────────────

# Legacy JSON paths — kept as module attributes for the one-time migration
# helper in state.migrations. Production reads/writes go through SQLite.
ALERT_FILE = Path(__file__).resolve().parent.parent / "cache" / "alerts" / "alerts.json"
RULES_FILE = Path(__file__).resolve().parent.parent / "cache" / "alerts" / "rules.json"
_MAX_STORED = 500


def _row_to_alert(row) -> ShippingAlert:
    """Map a sqlite3.Row from the alerts table to a ShippingAlert."""
    return ShippingAlert(
        alert_id=row["alert_id"],
        created_at=row["created_at"],
        alert_type=row["alert_type"],
        severity=row["severity"],
        title=row["title"],
        body=row["body"],
        ticker=row["ticker"] or "",
        route_id=row["route_id"] or "",
        port_locode=row["port_locode"] or "",
        value=float(row["value"]),
        threshold=float(row["threshold"]),
        change_pct=float(row["change_pct"]),
        acknowledged=bool(row["acknowledged"]),
    )


def _row_to_alert_full(row) -> dict:
    """Map a sqlite3.Row to a dict carrying every column.

    The dataclass projection in ``_row_to_alert`` drops the v14
    ``fire_count`` / ``last_fired_at`` columns and the v7 ``user_id``
    column to keep ``ShippingAlert`` back-compatible. Callers that
    NEED those fields (e.g. UI rendering of "fired 5 times in the
    last hour") use this helper to get the full row as a plain dict
    they can index by column name.

    ``fire_count`` falls back to 1 when the column is NULL (pre-v14
    legacy rows) so the UI never has to special-case the missing
    value. ``last_fired_at`` falls back to ``created_at`` for the
    same reason — pre-v14 rows have an empty ``last_fired_at`` but
    the implicit "fired once at created_at" reading is correct.
    """
    out: dict = {
        "alert_id":     row["alert_id"],
        "created_at":   row["created_at"],
        "alert_type":   row["alert_type"],
        "severity":     row["severity"],
        "title":        row["title"],
        "body":         row["body"],
        "ticker":       row["ticker"] or "",
        "route_id":     row["route_id"] or "",
        "port_locode":  row["port_locode"] or "",
        "value":        float(row["value"]),
        "threshold":    float(row["threshold"]),
        "change_pct":   float(row["change_pct"]),
        "acknowledged": bool(row["acknowledged"]),
    }
    # The v14 columns may be absent on a row from a pre-v14 schema if
    # tests bypass the migration; the try/except keeps the helper safe.
    try:
        fc = row["fire_count"]
        out["fire_count"] = int(fc) if fc is not None else 1
    except (IndexError, KeyError):
        out["fire_count"] = 1
    try:
        lf = row["last_fired_at"]
        out["last_fired_at"] = lf if lf else row["created_at"]
    except (IndexError, KeyError):
        out["last_fired_at"] = row["created_at"]
    # The v7 user_id column is also nice-to-have for callers that want
    # to render "alice's alert" tags. Same safe lookup.
    try:
        out["user_id"] = row["user_id"] or ""
    except (IndexError, KeyError):
        out["user_id"] = ""
    # The v19 bulk-ack metadata columns: optional free-form note +
    # ack-by attribution. Both are NULLable in SQLite so they may come
    # back as None — None falls through to None on the dict so callers
    # can distinguish "no note" (None) from "empty-string note" (a
    # caller passed ``note=''`` explicitly).
    try:
        out["acknowledged_note"] = row["acknowledged_note"]
    except (IndexError, KeyError):
        out["acknowledged_note"] = None
    try:
        out["acknowledged_by_user_id"] = row["acknowledged_by_user_id"]
    except (IndexError, KeyError):
        out["acknowledged_by_user_id"] = None
    return out


def get_alert_with_fire_count(alert_id: str) -> Optional[dict]:
    """Return the full alert row for ``alert_id`` as a dict (including
    fire_count + last_fired_at), or ``None`` if no row matches.

    Used by the UI to render a "fired N times in the last hour" badge
    on alerts that have collapsed multiple bounces into a single row.
    The query is NOT user-scoped — the alert_id is a uuid and the
    caller is expected to have authorized the lookup via the ack
    path; this helper is read-only and the ShippingAlert dataclass it
    returns alongside (via ``_row_to_alert_full``) does not include
    any field the caller did not already have access to via
    ``load_alerts``.
    """
    from state.db import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM alerts WHERE alert_id = ?",
            (alert_id,),
        ).fetchone()
    except Exception as exc:
        logger.warning(f"get_alert_with_fire_count: SQLite read failed: {exc}")
        return None
    if row is None:
        return None
    return _row_to_alert_full(row)


def _resolve_user_id(user_id: Optional[str]) -> str:
    """Pick the explicit ``user_id`` parameter, or fall back to the
    Streamlit session's ``current_user``.

    ``None`` means "caller did not specify" → consult the session via
    ``current_user_id()``. An explicit empty string means "force legacy
    mode" and is returned as-is. The session helper itself can never
    raise.
    """
    if user_id is None:
        from state.user_scope import current_user_id
        return current_user_id()
    return user_id


def save_alerts(
    alerts: list[ShippingAlert],
    *,
    user_id: Optional[str] = None,
    rule_id: Optional[str] = None,
) -> None:
    """Persist alerts to the SQLite store with two-layer dedup, then trim
    to _MAX_STORED keeping the newest (by created_at).

    Two dedup layers operate at different scopes:

      1. ``alert_id``-based INSERT OR IGNORE — blocks EXACT duplicates
         within (or across) save calls. Two writes of the SAME alert_id
         collapse to one row. This layer is unchanged from the original
         implementation.
      2. ``_dedup_key``-based time-window dedup — blocks NEAR duplicates
         of the same dedup_key (alert_type + severity + ticker +
         route_id + port_locode) within the last
         ``_DEDUP_WINDOW_MINUTES``. When a match is found, the existing
         row's ``fire_count`` is incremented, its ``last_fired_at`` is
         set to "now", and the ``value`` + ``change_pct`` are refreshed
         to the new (most-recent) reading. The new alert_id is
         discarded — the bounce shares the original row's id.

    Per-user scoping: dedup is per-user. Alice's BDI_MOVE/HIGH/(empty
    entity tuple) does NOT collide with Bob's BDI_MOVE/HIGH/(same).
    The exact-match user_id filter on the dedup lookup makes this so —
    note that the dedup query does NOT use the dual-set
    ``scope_filter_sql`` semantics; legacy rows belonging to ``""``
    only collide with new ``""`` writes, never with an authenticated
    user's writes.

    When ``user_id`` is ``None`` (default), the active Streamlit user's
    id is resolved via ``state.user_scope.current_user_id`` — outside
    Streamlit that returns ``""`` and rows land in the legacy global
    bucket exactly like before. Pass an explicit string to override
    (useful in tests). The user_id stamp is applied to every inserted
    row.

    ``rule_id`` (v18) stamps the originating AlertRule on every inserted
    row when supplied. ``None`` (default) means "this alert did not come
    from a rule" — the column lands NULL and the cooldown machinery in
    ``_in_cooldown`` skips it naturally. Callers that DO route through
    the rule engine (i.e. ``fire_rule``) pass the rule_id so the
    cooldown query can answer "when did rule X last successfully fire?".
    The dedup-bump UPDATE path intentionally does NOT overwrite rule_id
    on an existing row — the originating rule_id is set at INSERT time
    and is immutable across bumps.
    """
    if not alerts:
        return
    from state.db import get_connection

    uid = _resolve_user_id(user_id)
    conn = get_connection()
    now_iso = _now_iso()
    cutoff_iso = (
        datetime.now(timezone.utc) - timedelta(minutes=_DEDUP_WINDOW_MINUTES)
    ).isoformat()

    try:
        with conn:
            # Process alerts one at a time — the dedup lookup needs to
            # see the side-effects of earlier alerts in the same call
            # (so two identical-key alerts in one save_alerts list
            # collapse correctly: first inserts, second bumps the first's
            # fire_count). Doing this in Python rather than a giant
            # UPSERT keeps the logic readable and the bumped row's
            # value/change_pct refresh trivial.
            for a in alerts:
                key = _dedup_key(a)
                # Window-based dedup query: find the MOST RECENT
                # existing row with the same dedup_key created within
                # the window, for the SAME user_id (not the dual-set
                # scope — alice's row must not collide with bob's).
                existing = conn.execute(
                    """
                    SELECT alert_id, fire_count FROM alerts
                    WHERE alert_type = ?
                      AND severity   = ?
                      AND ticker     = ?
                      AND route_id   = ?
                      AND port_locode= ?
                      AND user_id    = ?
                      AND created_at >= ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (
                        a.alert_type, a.severity, a.ticker, a.route_id,
                        a.port_locode, uid, cutoff_iso,
                    ),
                ).fetchone()

                if existing is not None:
                    # Window-dedup hit: bump fire_count + last_fired_at +
                    # refresh value/change_pct on the existing row. The
                    # most-recent fire wins on the freshness fields so
                    # the UI shows the latest reading. Do NOT touch
                    # created_at — that anchors the window for future
                    # bumps and surfaces "when did this start" to the UI.
                    new_count = int(existing["fire_count"] or 1) + 1
                    conn.execute(
                        """
                        UPDATE alerts
                        SET fire_count    = ?,
                            last_fired_at = ?,
                            value         = ?,
                            change_pct    = ?
                        WHERE alert_id = ?
                        """,
                        (new_count, now_iso, a.value, a.change_pct,
                         existing["alert_id"]),
                    )
                    # _dedup_key matched — skip the INSERT entirely.
                    # The bounce shares the original row's alert_id.
                    continue

                # Window-dedup miss: fresh INSERT. INSERT OR IGNORE
                # preserves the original alert_id-PK dedup behaviour
                # (a caller passing the SAME alert_id twice in one
                # save call gets one row — the second is silently
                # dropped here, not at the dedup_key layer).
                #
                # ``rule_id`` is stamped from the caller-supplied
                # parameter (NULL when None — preserves the legacy
                # "alert came from a detection helper, not a rule"
                # path). The column is only used by the v18 cooldown
                # query, which already filters by rule_id = ?, so a
                # NULL row is invisible to it.
                conn.execute(
                    """
                    INSERT OR IGNORE INTO alerts
                      (alert_id, created_at, alert_type, severity, title, body,
                       ticker, route_id, port_locode, value, threshold, change_pct,
                       acknowledged, user_id, fire_count, last_fired_at, rule_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        a.alert_id, a.created_at, a.alert_type, a.severity,
                        a.title, a.body, a.ticker, a.route_id, a.port_locode,
                        a.value, a.threshold, a.change_pct,
                        1 if a.acknowledged else 0, uid, now_iso, rule_id,
                    ),
                )
            # Trim to _MAX_STORED keeping the newest created_at. SQLite's
            # ROWID order matches insertion order, but we sort by created_at
            # to be robust against backfilled or out-of-order writes.
            conn.execute(
                """
                DELETE FROM alerts
                WHERE alert_id IN (
                    SELECT alert_id FROM alerts
                    ORDER BY created_at DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (_MAX_STORED,),
            )
    except Exception as exc:
        logger.warning(f"save_alerts: SQLite write failed: {exc}")


def load_alerts(max_age_days: int = 30, *, user_id: Optional[str] = None) -> list[ShippingAlert]:
    """Load alerts created within the last ``max_age_days``.

    When ``user_id`` is ``None`` (default), falls back to the Streamlit
    session's ``current_user``. When the resolved id is the empty
    string (legacy / no session), every row is returned (the
    pre-multi-user behaviour). When it is non-empty, dual-set
    semantics apply — rows belonging to that user PLUS legacy
    ``user_id=''`` rows are returned together so existing data
    remains visible after the first login.
    """
    from state.db import get_connection
    from state.user_scope import scope_filter_sql

    uid = _resolve_user_id(user_id)
    scope_sql, scope_params = scope_filter_sql(uid)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT * FROM alerts WHERE created_at >= ? {scope_sql} "
            f"ORDER BY created_at DESC",
            (cutoff, *scope_params),
        ).fetchall()
    except Exception as exc:
        logger.warning(f"load_alerts: SQLite read failed: {exc}")
        return []
    return [_row_to_alert(r) for r in rows]


def acknowledge_alert(
    alert_id: str,
    *,
    user_id: Optional[str] = None,
    note: Optional[str] = None,
) -> None:
    """Mark a single alert as acknowledged.

    Also stamps ``acknowledged_at`` with the current ISO UTC timestamp
    so the alert-analytics module can compute median time-to-ack.
    Pre-v4 rows that were acked before this column existed keep an
    empty string here and are excluded from the time-to-ack metric.

    Honours per-user scoping: when ``user_id`` resolves to a non-empty
    string, the UPDATE matches only rows the user can SEE (their own
    rows PLUS legacy rows). A user cannot ACK another user's alert by
    knowing its id — the UPDATE silently no-ops.

    ``note`` (v19) — optional free-form string the operator attaches
    when acking the alert. ``None`` (the default) preserves the legacy
    "no note" behaviour and leaves the ``acknowledged_note`` column
    NULL. An empty string is persisted as the empty string (NOT NULL)
    so callers can distinguish "operator explicitly attached an empty
    note" from "no note ever set". The note value is NEVER included in
    logger output — it may contain operator-sensitive context.

    ``user_id`` (v19) — when resolved to a non-empty string, also gets
    stamped into the new ``acknowledged_by_user_id`` column so the
    alert row itself can answer "who acked this?" without a join
    against the audit log.
    """
    from state.db import get_connection
    from state.user_scope import scope_filter_sql

    uid = _resolve_user_id(user_id)
    scope_sql, scope_params = scope_filter_sql(uid)
    conn = get_connection()
    ack_ts = _now_iso()
    # v19 column stamps: store NULL for the user-id column when the
    # resolved uid is empty so we do not pollute legacy rows with an
    # empty-string attribution; the note column accepts None → NULL.
    ack_by = uid if uid else None
    try:
        with conn:
            conn.execute(
                f"UPDATE alerts SET acknowledged = 1, acknowledged_at = ?, "
                f"acknowledged_note = ?, acknowledged_by_user_id = ? "
                f"WHERE alert_id = ? {scope_sql}",
                (ack_ts, note, ack_by, alert_id, *scope_params),
            )
    except Exception as exc:
        logger.warning(f"acknowledge_alert: SQLite update failed: {exc}")
    # Audit-log the ACK. record_audit never raises but we still wrap in
    # try/except here as belt-and-braces — this hook sits inside the
    # critical user-action path and an audit-write bug must never block
    # the ACK from completing.
    #
    # The note (when supplied) is truncated to 200 chars in the audit
    # payload to keep the audit log compact — the FULL note still
    # persists on the alert row above. The note string is intentionally
    # NOT logged via logger.* anywhere in this function — operators may
    # paste sensitive context into the note field.
    try:
        from auth.audit import record_audit
        detail: dict = {"acknowledged_at": ack_ts}
        if note is not None:
            detail["note_truncated_to_200_chars"] = str(note)[:200]
        record_audit(
            "ack_alert",
            entity_type="alert",
            entity_id=alert_id,
            detail=detail,
            user_id=user_id,
        )
    except Exception:  # noqa: BLE001
        pass


def acknowledge_all(*, user_id: Optional[str] = None) -> None:
    """Mark every alert in the store as acknowledged.

    Sets ``acknowledged_at`` for every row whose flag flips from 0 → 1
    in this call. Rows that were already acked keep their original
    acknowledged_at (we only fill in the timestamp for rows we are
    transitioning here — overwriting would lie about WHEN the user
    acked the alert).

    Honours per-user scoping the same way as ``acknowledge_alert``:
    when ``user_id`` resolves to a non-empty string, only rows in the
    user's scope (own + legacy) are ACK'd.
    """
    from state.db import get_connection
    from state.user_scope import scope_filter_sql

    uid = _resolve_user_id(user_id)
    scope_sql, scope_params = scope_filter_sql(uid)
    conn = get_connection()
    ack_ts = _now_iso()
    affected = 0
    try:
        with conn:
            cur = conn.execute(
                f"UPDATE alerts SET acknowledged = 1, acknowledged_at = ? "
                f"WHERE acknowledged = 0 {scope_sql}",
                (ack_ts, *scope_params),
            )
            affected = int(cur.rowcount or 0)
    except Exception as exc:
        logger.warning(f"acknowledge_all: SQLite update failed: {exc}")
    # Audit-log the bulk ACK. count records HOW MANY alerts flipped, so
    # security review can spot one-click "ack everything" sweeps in the
    # event log. Wrapped in try/except as belt-and-braces (record_audit
    # itself never raises).
    try:
        from auth.audit import record_audit
        record_audit(
            "ack_all_alerts",
            detail={"count": affected, "acknowledged_at": ack_ts},
            user_id=user_id,
        )
    except Exception:  # noqa: BLE001
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  Bulk acknowledgement (v19)
#
#  Single-ack costs one click per row. When 30 LOW alerts fire, that is
#  30 clicks. The bulk helpers below ack a SET of alerts in one round-
#  trip and record ONE audit event covering the whole batch. Per-user
#  scoping is required — no operator can ack another's alerts even with
#  the alert_id, just like ``acknowledge_alert``.
# ─────────────────────────────────────────────────────────────────────────────

# Notes are persisted in full on the alert row (see ``acknowledged_note``).
# The audit-event copy is truncated to this length so a long note does
# not bloat the audit log, which is queried more often than a single
# alert's note. The constant is exposed at module scope so tests can
# pin the truncation length without monkey-patching internals.
_BULK_ACK_AUDIT_NOTE_MAXLEN: int = 200


def bulk_acknowledge_alerts(
    alert_ids: list[str],
    *,
    note: Optional[str] = None,
    user_id: Optional[str] = None,
) -> dict:
    """Mark many alerts as acknowledged in a single round-trip.

    Returns a dict with four integer keys whose sum equals
    ``len(alert_ids)`` (every input id is counted exactly once):

      * ``acked``                 — rows flipped from unack → ack by
                                     this call.
      * ``skipped_already_acked`` — rows that were already acked in
                                     the user's scope. The note + ack
                                     timestamp + ack-by-user-id on
                                     those rows are NOT overwritten —
                                     bulk-ack is additive, not
                                     destructive, so a row acked by
                                     alice yesterday with note "FOO"
                                     stays acked by alice with note
                                     "FOO" when bob's bulk-ack pass
                                     hits it.
      * ``not_found``             — ids the caller passed that do not
                                     exist in the user's scope. Could
                                     mean the alert was pruned, the
                                     id was mistyped, or the row
                                     belongs to a different user
                                     (per-user scoping is enforced).
      * ``failed``                — ids the caller passed that the
                                     UPDATE round-trip could not
                                     classify (almost always 0; bumps
                                     to ``len(alert_ids)`` on the
                                     except-path when SQLite errors).

    Per-user scoping mirrors ``acknowledge_alert`` exactly: when
    ``user_id`` resolves to a non-empty string, only rows whose
    ``user_id`` matches that user OR the legacy ``''`` bucket are
    eligible. A user passing alice's alert_id silently lands the id
    in ``not_found`` rather than acking it.

    NEVER raises. Every code path is wrapped in try/except; on any
    catastrophic failure the function returns
    ``{"acked": 0, "skipped_already_acked": 0, "not_found": 0,
    "failed": len(alert_ids)}`` so the caller can surface "all N ack
    attempts failed" without crashing the UI.

    Records ONE audit event with ``action='bulk_acknowledge'`` covering
    the whole batch — operationally clearer than N events, and lets a
    security review answer "did someone bulk-ack 50 CRITICALs at 3 a.m.?"
    with a single row. The audit detail carries the input id list, the
    output counts, and (when supplied) the note TRUNCATED to 200 chars.
    The full note is on the alert rows themselves. The note value is
    NEVER passed to ``logger.*`` — it may contain operator-sensitive
    context.

    Empty ``alert_ids`` list short-circuits: returns all-zero counts and
    does NOT record an audit event (a no-op call is not auditable
    activity).
    """
    # Defensive normalization — accept any iterable of ids but coerce
    # to a deduplicated list of non-empty strings so the IN-clause is
    # well-formed. dict.fromkeys preserves insertion order while
    # dropping duplicates so the audit payload reads in caller order.
    try:
        ids_in: list[str] = list(alert_ids or [])
    except Exception:
        ids_in = []
    # Drop None / empty / non-string entries so the IN-clause is clean.
    clean_ids: list[str] = [
        i for i in dict.fromkeys(ids_in) if isinstance(i, str) and i
    ]
    n_input = len(ids_in)

    # Empty-input short-circuit. No DB hit, no audit row — a no-op call
    # is invisible to security review on purpose (otherwise every UI
    # render of an empty multiselect would log a spurious bulk_ack
    # event).
    if not clean_ids:
        return {
            "acked": 0,
            "skipped_already_acked": 0,
            "not_found": n_input,
            "failed": 0,
        }

    from state.user_scope import scope_filter_sql

    uid = _resolve_user_id(user_id)
    scope_sql, scope_params = scope_filter_sql(uid)
    ack_ts = _now_iso()
    ack_by = uid if uid else None

    # Build the IN-clause placeholder string ONCE — the same shape is
    # used by both the pre-UPDATE classification query AND the UPDATE.
    placeholders = ",".join(["?"] * len(clean_ids))

    try:
        # get_connection() lives inside the try so a broken
        # connection helper (e.g. a forced "database is locked")
        # also lands in the failed bucket rather than escaping. The
        # whole function is by-contract non-raising.
        from state.db import get_connection
        conn = get_connection()
        with conn:
            # First classify each input id under the user's scope:
            #   already-acked → goes to skipped_already_acked
            #   unack         → eligible for the UPDATE
            #   not in scope  → goes to not_found
            #
            # One SELECT (not N) — that is the whole point of the bulk
            # helper. The IN-clause + scope filter rides on the
            # alert_id PK + the existing idx_alerts_unacknowledged
            # index, so even a 500-id batch round-trips fast.
            rows = conn.execute(
                f"SELECT alert_id, acknowledged FROM alerts "
                f"WHERE alert_id IN ({placeholders}) {scope_sql}",
                (*clean_ids, *scope_params),
            ).fetchall()
            visible: dict[str, int] = {
                r["alert_id"]: int(r["acknowledged"]) for r in rows
            }
            to_ack: list[str] = [i for i in clean_ids if visible.get(i) == 0]
            already_acked = sum(1 for i in clean_ids if visible.get(i) == 1)
            not_found = sum(1 for i in clean_ids if i not in visible)

            if to_ack:
                # One UPDATE for the whole eligible set. The scope
                # filter is reapplied here belt-and-braces — the
                # classification SELECT above already filtered, but
                # the UPDATE is the authoritative write and must not
                # depend on the SELECT having executed atomically with
                # it (we use an explicit ``with conn:`` block, but the
                # extra WHERE costs nothing and defends against future
                # refactors that split the SELECT off).
                up_placeholders = ",".join(["?"] * len(to_ack))
                conn.execute(
                    f"UPDATE alerts "
                    f"SET acknowledged = 1, "
                    f"    acknowledged_at = ?, "
                    f"    acknowledged_note = ?, "
                    f"    acknowledged_by_user_id = ? "
                    f"WHERE alert_id IN ({up_placeholders}) {scope_sql}",
                    (ack_ts, note, ack_by, *to_ack, *scope_params),
                )
            acked = len(to_ack)
    except Exception as exc:
        # Catastrophic write failure. Count every input id as "failed"
        # so the sum still equals len(alert_ids) — the operator's UI
        # can show "0 acked / 0 skipped / 0 not found / N failed" and
        # the caller can surface a single error toast.
        logger.warning(
            f"bulk_acknowledge_alerts: SQLite update failed: {exc}"
        )
        return {
            "acked": 0,
            "skipped_already_acked": 0,
            "not_found": 0,
            "failed": n_input,
        }

    # We dropped duplicate / empty entries from ``ids_in`` above; any
    # difference between ``n_input`` and the four counted buckets is
    # those discarded entries. Bucket them under ``not_found`` so the
    # sum invariant (acked + skipped + not_found + failed == n_input)
    # still holds — a caller passing the same id twice or an empty
    # string sees the second copy / empty land in not_found rather
    # than silently disappearing.
    counted = acked + already_acked + not_found
    discarded = max(0, n_input - counted)
    not_found += discarded

    # Audit-log the bulk ack. ONE event, not N. The detail carries the
    # input ids + output counts so a security review can reconstruct
    # "what got acked" without joining against the alerts table. The
    # note (when supplied) is truncated to 200 chars in the detail —
    # the full note is on each acked row. The note is NEVER passed to
    # logger.* anywhere in this function.
    try:
        from auth.audit import record_audit
        detail: dict = {
            "count": acked,
            "skipped_already_acked": already_acked,
            "not_found": not_found,
            "ids": list(clean_ids),
            "acknowledged_at": ack_ts,
        }
        if note is not None:
            detail["note_truncated_to_200_chars"] = (
                str(note)[:_BULK_ACK_AUDIT_NOTE_MAXLEN]
            )
        record_audit(
            "bulk_acknowledge",
            entity_type="alert",
            detail=detail,
            user_id=user_id,
        )
    except Exception:  # noqa: BLE001
        # record_audit is by-contract non-raising; the extra wrapper
        # is belt-and-braces in case a future hook on the audit path
        # leaks. We deliberately do NOT log the note here.
        pass

    return {
        "acked": acked,
        "skipped_already_acked": already_acked,
        "not_found": not_found,
        "failed": 0,
    }


def bulk_acknowledge_alerts_by_filter(
    *,
    severity: Optional[str] = None,
    alert_type: Optional[str] = None,
    before_iso: Optional[str] = None,
    user_id: Optional[str] = None,
    note: Optional[str] = None,
) -> dict:
    """Convenience helper: resolve a set of alert_ids by WHERE filter,
    then delegate to ``bulk_acknowledge_alerts``.

    The UI patterns this enables:

      * "Ack every CRITICAL"           → severity='CRITICAL'
      * "Ack every BDI_MOVE"           → alert_type='BDI_MOVE'
      * "Ack everything older than 7d" → before_iso=(now - 7d).isoformat()
      * Combined: "Ack every LOW from yesterday or earlier" →
        severity='LOW', before_iso=(now - 1d).isoformat()

    Filter semantics:

      * ``severity``   — TIER-OR-WORSE match. Passing 'HIGH' acks rows
                          whose severity is HIGH OR CRITICAL; passing
                          'MEDIUM' acks MEDIUM + HIGH + CRITICAL; passing
                          'LOW' acks everything. This matches the
                          operator's mental model ("ack at least HIGH")
                          and mirrors the severity-tier semantics used
                          by the saved-filter payload in
                          ``ui/tab_alerts.py``. None means "any severity".
                          An unknown severity string is treated as exact
                          match (the IN-clause degrades to a single
                          value) — safer than the alternative of
                          silently expanding to nothing.
      * ``alert_type`` — EXACT match.
      * ``before_iso`` — keeps rows whose ``created_at < before_iso``.
                          ISO-8601 UTC string. The comparison is the
                          string compare SQLite does natively; ISO-8601
                          is fixed-width and tz-aware so lex order
                          matches chronological order.

    Only UNACKED rows are selected for the bulk pass — already-acked
    rows are excluded at the SELECT stage so they do NOT show up in
    ``skipped_already_acked`` (the by-filter helper is "ack everything
    that still needs acking under this filter", not "re-process every
    matching row"). This is the SHAPE difference from
    ``bulk_acknowledge_alerts``: that one takes a caller-supplied id
    list and reports back what happened to each id; this one filters
    THEN delegates, so ``skipped_already_acked`` and ``not_found`` are
    always 0.

    Per-user scoping mirrors the other ack helpers exactly: rows
    belonging to the resolved user OR the legacy ``''`` bucket are
    eligible.

    Returns the same dict shape as ``bulk_acknowledge_alerts``. NEVER
    raises: a SQLite read failure during the WHERE-clause SELECT
    returns ``{"acked": 0, "skipped_already_acked": 0, "not_found": 0,
    "failed": 0}`` (zero of everything — there was no id list to fail
    on).

    No filter at all (every arg defaulting to None) is treated as
    "ack every unacked row in your scope" — convenient but the UI is
    expected to gate this behind a confirmation since it is identical
    to ``acknowledge_all``. We do NOT short-circuit to
    ``acknowledge_all`` here because the bulk helper records a
    different audit event (``bulk_acknowledge`` vs ``ack_all_alerts``)
    and carries the id list in the audit detail.
    """
    from state.db import get_connection
    from state.user_scope import scope_filter_sql

    uid = _resolve_user_id(user_id)
    scope_sql, scope_params = scope_filter_sql(uid)

    # Build the WHERE clause dynamically. Start with "unacked rows in
    # the user's scope"; each provided filter adds an AND clause.
    where_parts: list[str] = ["acknowledged = 0"]
    where_params: list = []
    if severity is not None and isinstance(severity, str) and severity:
        # Tier-or-worse expansion: pull the requested severity AND every
        # more-severe tier so "ack at least HIGH" includes CRITICAL.
        # The ordering comes from ``_SEVERITY_ORDER`` (CRITICAL=0,
        # HIGH=1, MEDIUM=2, LOW=3) — every row whose rank is <= the
        # requested rank qualifies. Unknown severity strings fall back
        # to exact match.
        req_rank = _SEVERITY_ORDER.get(severity)
        if req_rank is None:
            where_parts.append("severity = ?")
            where_params.append(severity)
        else:
            tiers = [
                name for name, rank in _SEVERITY_ORDER.items()
                if rank <= req_rank
            ]
            placeholders = ",".join(["?"] * len(tiers))
            where_parts.append(f"severity IN ({placeholders})")
            where_params.extend(tiers)
    if alert_type is not None and isinstance(alert_type, str) and alert_type:
        where_parts.append("alert_type = ?")
        where_params.append(alert_type)
    if before_iso is not None and isinstance(before_iso, str) and before_iso:
        where_parts.append("created_at < ?")
        where_params.append(before_iso)

    where_clause = " AND ".join(where_parts)
    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT alert_id FROM alerts WHERE {where_clause} {scope_sql}",
            (*where_params, *scope_params),
        ).fetchall()
    except Exception as exc:
        logger.warning(
            f"bulk_acknowledge_alerts_by_filter: SELECT failed: {exc}"
        )
        return {
            "acked": 0,
            "skipped_already_acked": 0,
            "not_found": 0,
            "failed": 0,
        }

    ids = [r["alert_id"] for r in rows]
    # Delegate to the id-list helper for the actual UPDATE + audit. The
    # delegate handles the empty-list short-circuit (returns all zeros
    # and skips the audit event) so an "ack by filter" pass that finds
    # nothing does not pollute the audit log.
    return bulk_acknowledge_alerts(ids, note=note, user_id=user_id)


def get_unread_count(*, user_id: Optional[str] = None) -> int:
    """Count unacknowledged alerts.

    Honours per-user scoping — when ``user_id`` resolves to a non-empty
    string, only rows in the user's scope (own + legacy) are counted.
    """
    from state.db import get_connection
    from state.user_scope import scope_filter_sql

    uid = _resolve_user_id(user_id)
    scope_sql, scope_params = scope_filter_sql(uid)
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM alerts WHERE acknowledged = 0 {scope_sql}",
            scope_params,
        ).fetchone()
        return int(row["n"]) if row else 0
    except Exception as exc:
        logger.warning(f"get_unread_count: SQLite read failed: {exc}")
        return 0


def prune_old_alerts(
    retention_days: int = 180,
    *,
    only_acknowledged: bool = True,
) -> int:
    """Delete alerts older than ``retention_days`` from the alerts table.

    Companion to ``prune_old_calls`` / ``prune_old_events`` / etc. By
    default only ACKNOWLEDGED alerts are pruned — unacknowledged ones
    stay around indefinitely so a 10-month-old unack'd CRITICAL doesn't
    disappear from the operator's view because of routine retention.

    Pass ``only_acknowledged=False`` to also prune unacked rows past
    the cutoff (admin-only — useful for one-off cleanup of legacy data).

    Args:
        retention_days: rows with created_at < (now - retention_days)
                        are eligible for deletion. Defaults to 180 days
                        (~6 months). Negative values are a no-op (returns 0).
        only_acknowledged: when True (the default), only acknowledged
                           rows are deleted. When False, everything past
                           the cutoff is deleted regardless of ack state.

    Returns:
        The count of rows deleted. Never raises (returns 0 on error).
    """
    if retention_days < 0:
        return 0
    from state.db import get_connection

    cutoff_iso = (datetime.now(timezone.utc)
                  - timedelta(days=retention_days)).isoformat()
    conn = get_connection()
    try:
        with conn:
            if only_acknowledged:
                cur = conn.execute(
                    "DELETE FROM alerts WHERE created_at < ? AND acknowledged = 1",
                    (cutoff_iso,),
                )
            else:
                cur = conn.execute(
                    "DELETE FROM alerts WHERE created_at < ?",
                    (cutoff_iso,),
                )
        return int(cur.rowcount or 0)
    except Exception as exc:
        logger.warning(f"prune_old_alerts: SQLite delete failed: {exc}")
        return 0


# ─────────────────────────────────────────────────────────────────────────────
#  Rule persistence (separate from alert persistence — rules are user-authored
#  configuration, alerts are the fired events those rules produce).
# ─────────────────────────────────────────────────────────────────────────────

def save_rules(rules: list[dict], *, user_id: Optional[str] = None) -> None:
    """Persist the user's alert-rule list to the SQLite store.

    Rules are stored as JSON blobs keyed by rule_id (or id) — matching the
    in-memory shape used by ``ui/tab_alerts.py``'s session state. The typed
    ``AlertRule`` dataclass is reserved for future use; the dict shape
    keeps the editor UI flexible while still being durable across sessions.

    Replaces the rule set for the given user_id scope:
      * Legacy mode (``user_id=""``, resolved or explicit): wipes EVERY
        row in the table and reinserts — matches the original
        write-everything semantics callers rely on.
      * Per-user mode (non-empty ``user_id``): wipes only this user's
        rows AND legacy ``user_id=''`` rows (since the user just
        adopted them via the dual-set read), then reinserts stamped
        with the user's id.
    """
    from state.db import get_connection
    from state.user_scope import scope_filter_sql

    uid = _resolve_user_id(user_id)
    conn = get_connection()
    try:
        with conn:
            if uid:
                # Per-user replace: drop rows the user "owns" (their own
                # + legacy) and rewrite them under this user. WHERE 1=1
                # gives the scope fragment something to AND against.
                scope_sql, scope_params = scope_filter_sql(uid)
                conn.execute(
                    f"DELETE FROM alert_rules WHERE 1=1 {scope_sql}",
                    scope_params,
                )
            else:
                # Legacy global replace.
                conn.execute("DELETE FROM alert_rules")
            rows = []
            for r in rules:
                if not isinstance(r, dict):
                    continue
                rule_id = r.get("rule_id") or r.get("id")
                if not rule_id:
                    continue
                rows.append((str(rule_id), json.dumps(r, default=str), uid))
            if rows:
                conn.executemany(
                    "INSERT INTO alert_rules (rule_id, data, user_id) "
                    "VALUES (?, ?, ?)",
                    rows,
                )
    except Exception as exc:
        logger.warning(f"save_rules: SQLite write failed: {exc}")
    # Audit-log the rule replacement. Count is computed from the
    # caller-supplied rules list rather than the row count we ended up
    # inserting — security review wants to see "user saved N rules",
    # not "the executemany wrote N filtered rows".
    try:
        from auth.audit import record_audit
        record_audit(
            "save_rules",
            detail={"count": len(rules) if isinstance(rules, list) else 0},
            user_id=user_id,
        )
    except Exception:  # noqa: BLE001
        pass


def load_rules(*, user_id: Optional[str] = None) -> list[dict]:
    """Load the persisted user rule list. Returns [] if no rules exist
    or the read fails — callers can fall back to defaults in that case.

    Honours per-user scoping with the same dual-set semantics as
    ``load_alerts``: when ``user_id`` resolves to a non-empty string,
    rows belonging to that user PLUS legacy ``user_id=''`` rows are
    returned. The empty-string case returns every row (legacy
    behaviour).
    """
    from state.db import get_connection
    from state.user_scope import scope_filter_sql

    uid = _resolve_user_id(user_id)
    scope_sql, scope_params = scope_filter_sql(uid)
    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT data FROM alert_rules WHERE 1=1 {scope_sql} "
            f"ORDER BY rule_id",
            scope_params,
        ).fetchall()
    except Exception as exc:
        logger.warning(f"load_rules: SQLite read failed: {exc}")
        return []

    out: list[dict] = []
    for r in rows:
        try:
            parsed = json.loads(r["data"])
            if isinstance(parsed, dict):
                out.append(parsed)
        except Exception:
            continue
    return out


def normalize_rule(rule_dict: dict) -> dict:
    """Backfill optional fields on a rule dict.

    Older rule blobs predate the ``target_channels`` field (added when
    rule-to-channel routing landed) and the v18 ``cooldown_minutes``
    field. Callers that need a normalized shape — e.g.
    ``deliver_pending_for_rule`` or ``fire_rule`` — should run rules
    through this helper first. ``load_rules()`` itself is intentionally
    NOT auto-normalizing so the save→load round-trip stays byte-exact
    for legacy callers that compare the loaded list against what they
    saved.

    Returns the same dict object with:
      * ``target_channels`` coerced to a list[str] (empty == 'all
        eligible channels', the legacy behaviour).
      * ``cooldown_minutes`` coerced to a non-negative int. Values
        that fail to coerce fall back to 0 (no cooldown). Negative
        values are clamped to 0 — "cooldown of -5 minutes" is
        nonsensical and a hand-edited blob with a stray minus sign
        should not turn into "fire forever, never cool down".

    Non-dict input passes through unchanged.
    """
    if not isinstance(rule_dict, dict):
        return rule_dict
    tc = rule_dict.get("target_channels")
    if not isinstance(tc, list):
        rule_dict["target_channels"] = []
    else:
        # Coerce to strings + drop any non-string entries so a stray
        # int/None in a hand-edited blob cannot break channel matching.
        rule_dict["target_channels"] = [x for x in tc if isinstance(x, str)]
    # Cooldown coercion (v18). The blob may carry an int, a numeric
    # string, a bool (Python: True coerces to 1, False to 0 — both
    # acceptable cooldown values), a None, or a garbage string. We
    # accept anything int() will swallow, clamp the result to >= 0,
    # and fall back to 0 (no cooldown) on TypeError/ValueError. The
    # default key ``cooldown_minutes`` is created if missing — that's
    # the v18 contract: every rule has a cooldown_minutes field
    # going forward, with 0 meaning "no cooldown".
    raw_cd = rule_dict.get("cooldown_minutes", 0)
    try:
        # int(bool) and int("10") and int(10.0) all work; int("abc")
        # raises ValueError; int(None) raises TypeError. The int()
        # cast is the canonical "permissive numeric coerce" idiom.
        cd = int(raw_cd)
    except (TypeError, ValueError):
        cd = 0
    rule_dict["cooldown_minutes"] = max(0, cd)
    return rule_dict


def reset_rules() -> None:
    """Drop every rule. The next ``load_rules()`` returns []
    so the caller can re-seed with its default list."""
    from state.db import get_connection

    conn = get_connection()
    try:
        with conn:
            conn.execute("DELETE FROM alert_rules")
    except Exception as exc:
        logger.warning(f"reset_rules: SQLite delete failed: {exc}")
    # Audit-log the rule wipe. No detail payload — this is a single
    # boolean event ("user reset rules") with no useful per-call body.
    try:
        from auth.audit import record_audit
        record_audit("reset_rules")
    except Exception:  # noqa: BLE001
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  Per-rule cooldown (v18)
#
#  An AlertRule whose condition stays tripped used to fire on every
#  evaluation, spamming downstream channels. v18 introduces a per-rule
#  ``cooldown_minutes`` field that suppresses the SAME rule_id from
#  firing more than once per cooldown window (per user). Cooldown is
#  orthogonal to the v14 dedup_key collapse: dedup merges bounces of
#  an alert that ALREADY fired; cooldown stops the rule from firing
#  in the first place.
# ─────────────────────────────────────────────────────────────────────────────

# Key the counter under a stable kv_state row so the operator overview /
# data health tabs can surface "N alert fires suppressed by cooldown
# this run" without us needing a dedicated table. The value is the
# stringified running count (kv_state.value is TEXT) — callers parse it
# with int() and treat a missing row as 0.
_COOLDOWN_SUPPRESSED_KEY: str = "alerts_suppressed_by_cooldown"


def _in_cooldown(
    rule_id: str,
    cooldown_minutes: int,
    *,
    user_id: Optional[str] = None,
) -> bool:
    """Return True when ``rule_id`` is INSIDE its cooldown window for
    ``user_id`` and a fresh fire should be suppressed.

    A rule is in cooldown when there exists a prior alert in the alerts
    table with the same ``rule_id`` and ``user_id`` whose ``created_at``
    is within the last ``cooldown_minutes`` minutes. The check uses
    ``MAX(created_at)`` so multiple prior fires within the window
    behave the same as one — only the most-recent fire anchors the
    window.

    Short-circuits to False in two cases:
      * ``cooldown_minutes <= 0`` — explicit "no cooldown configured",
        which is the v18 default for legacy rules.
      * empty / falsy ``rule_id`` — the cooldown table is keyed by
        rule_id so a missing rule_id has no meaningful prior to look
        up. ``fire_rule`` always supplies a non-empty rule_id; this
        guard is belt-and-braces for callers that wire the helper
        directly.

    Per-user scoping is by exact match (NOT the dual-set scope
    semantics used by ``load_alerts``): alice's prior fire of rule X
    does NOT trip bob's cooldown on the same rule_id. The legacy
    bucket (``user_id=""``) only collides with other legacy fires.
    """
    if cooldown_minutes <= 0:
        return False
    if not rule_id:
        return False
    from state.db import get_connection

    uid = _resolve_user_id(user_id)
    cutoff_iso = (
        datetime.now(timezone.utc) - timedelta(minutes=cooldown_minutes)
    ).isoformat()
    conn = get_connection()
    try:
        # MAX over the per-row "most recent activity" timestamp:
        # ``last_fired_at`` when populated (v14+ row that may have
        # been dedup-bumped), else fall back to ``created_at`` (the
        # original insertion time, also correct as "when did this
        # rule last fire" for rows that have only been INSERTed and
        # never bumped). The COALESCE in the SELECT makes this
        # robust to either column being empty.
        #
        # Comparing against ``last_fired_at`` (and not just
        # ``created_at``) matters when the dedup window is LONGER
        # than the cooldown window: a row that keeps getting dedup-
        # bumped on every evaluation would otherwise look like
        # "fired once long ago" to a cooldown check that only saw
        # created_at, and the cooldown gate would let every bump
        # through.
        row = conn.execute(
            """
            SELECT MAX(
                CASE
                    WHEN last_fired_at IS NOT NULL AND last_fired_at != ''
                        THEN last_fired_at
                    ELSE created_at
                END
            ) AS last_fire
            FROM alerts
            WHERE rule_id = ?
              AND user_id = ?
            """,
            (rule_id, uid),
        ).fetchone()
    except Exception as exc:
        # Read failure → fail OPEN (allow the fire). A cooldown that
        # silently breaks because of a DB hiccup is far less harmful
        # than a cooldown that silently blocks every alert because of
        # one. Same defensive posture as the rest of save_alerts /
        # load_alerts.
        logger.warning(f"_in_cooldown: SQLite read failed: {exc}")
        return False
    if row is None:
        return False
    last_fire = row["last_fire"]
    if not last_fire:
        # No prior fire exists for this rule_id under this user — no
        # cooldown to enforce.
        return False
    # last_fire is ISO-8601 UTC; string comparison against cutoff_iso
    # is correct because the format is fixed-width and timezone-aware.
    return last_fire >= cutoff_iso


def _bump_suppressed_counter() -> None:
    """Increment the kv_state counter of cooldown-suppressed fires.

    Read-modify-write under the connection's autocommit boundary —
    SQLite serializes writers via WAL so the increment is atomic for
    a single-process app. Best-effort: any failure is swallowed and
    logged because the counter is for operator-overview telemetry,
    not correctness.
    """
    from state.db import get_connection

    conn = get_connection()
    now_iso = _now_iso()
    try:
        row = conn.execute(
            "SELECT value FROM kv_state WHERE key = ?",
            (_COOLDOWN_SUPPRESSED_KEY,),
        ).fetchone()
        try:
            current = int(row["value"]) if row else 0
        except (TypeError, ValueError):
            # A corrupt counter value resets to 0 so a single bad
            # write does not jam the increment path forever.
            current = 0
        conn.execute(
            "INSERT OR REPLACE INTO kv_state (key, value, updated_at) "
            "VALUES (?, ?, ?)",
            (_COOLDOWN_SUPPRESSED_KEY, str(current + 1), now_iso),
        )
    except Exception as exc:
        logger.warning(f"_bump_suppressed_counter: kv_state write failed: {exc}")


def get_suppressed_by_cooldown_count() -> int:
    """Return the cumulative count of rule fires suppressed by
    cooldown since the app started writing the counter (or since the
    last manual reset). Returns 0 when the kv_state row does not yet
    exist."""
    from state.db import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM kv_state WHERE key = ?",
            (_COOLDOWN_SUPPRESSED_KEY,),
        ).fetchone()
    except Exception as exc:
        logger.warning(f"get_suppressed_by_cooldown_count: read failed: {exc}")
        return 0
    if row is None:
        return 0
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return 0


def fire_rule(
    rule: dict,
    alerts: list[ShippingAlert],
    *,
    user_id: Optional[str] = None,
) -> bool:
    """Persist + dispatch the given alerts under the supplied rule,
    respecting the rule's per-rule cooldown (v18).

    Returns True when the alerts were persisted (the fire was
    permitted), False when the rule was suppressed by cooldown.

    The cooldown gate is checked ONCE at the entry of the function
    using ``_in_cooldown`` and the rule's normalized cooldown_minutes.
    When the gate trips:
      * NO row is inserted (the underlying condition is still True;
        we just decline to re-notify).
      * The suppressed counter is incremented via kv_state.
      * A logger.info line records the suppression for the operator
        log trail.
      * The function returns False so the caller can short-circuit
        any downstream dispatch (Slack / email / webhook).

    Dispatch itself is NOT performed here — that lives in
    ``engine.alert_delivery``. ``fire_rule`` is responsible only for
    the gate + the persistence stamp. Callers wire dispatch downstream
    of a True return.

    The rule dict is normalized in-place (cooldown_minutes coerced /
    target_channels coerced) so callers do not need to pre-normalize.
    """
    # Normalize so cooldown_minutes is a clean non-negative int and
    # target_channels is a clean list[str]. Mutates rule in-place,
    # which is fine — callers re-load from save_rules anyway.
    normalize_rule(rule)
    rule_id = str(rule.get("rule_id") or rule.get("id") or "")
    cooldown = int(rule.get("cooldown_minutes", 0))

    if _in_cooldown(rule_id, cooldown, user_id=user_id):
        # Gate tripped — suppress the fire. The condition that
        # produced these alerts is still True; we just decline to
        # re-notify until the window expires. The counter bump lets
        # the operator-overview surface "N suppressions" without a
        # dedicated table.
        logger.info(
            f"rule {rule_id} suppressed by cooldown "
            f"({cooldown}m window, user={_resolve_user_id(user_id)})"
        )
        _bump_suppressed_counter()
        return False

    # Cooldown gate cleared — persist with the rule_id stamp so the
    # NEXT cooldown check can see this fire as the prior. Empty
    # alerts list is a legitimate fire (the rule ran but produced no
    # rows): we still return True so the caller's audit trail records
    # an evaluation, but no INSERT happens.
    if alerts:
        save_alerts(alerts, user_id=user_id, rule_id=rule_id)
    return True
