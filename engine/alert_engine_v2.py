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
    """Fire if any freight rate moves >threshold_pct in 7 days."""
    alerts: list[ShippingAlert] = []
    try:
        import pandas as pd
    except ImportError:
        return alerts

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
        if abs(chg) < threshold_pct:
            continue

        direction = "surged" if chg > 0 else "collapsed"
        severity = "CRITICAL" if abs(chg) >= threshold_pct * 2 else "HIGH"
        label = str(route_id).replace("_", " ").title()
        alerts.append(_make(
            alert_type="RATE_SURGE",
            severity=severity,
            title=f"Rate {direction.title()}: {label} ({chg:+.1f}% / 7d)",
            body=(
                f"Freight rates on {label} have {direction} {abs(chg):.1f}% over the past 7 days, "
                f"reaching ${current:,.0f}/FEU against the {threshold_pct:.0f}% threshold. "
                f"{'Consider booking forward capacity before further escalation.' if chg > 0 else 'Spot market opportunity — delay forward bookings if possible.'}"
            ),
            route_id=str(route_id),
            value=current,
            threshold=threshold_pct,
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


def save_alerts(alerts: list[ShippingAlert], *, user_id: Optional[str] = None) -> None:
    """Persist alerts to the SQLite store; dedupe by alert_id; trim to
    _MAX_STORED keeping the newest (by created_at).

    When ``user_id`` is ``None`` (default), the active Streamlit user's
    id is resolved via ``state.user_scope.current_user_id`` — outside
    Streamlit that returns ``""`` and rows land in the legacy global
    bucket exactly like before. Pass an explicit string to override
    (useful in tests). The user_id stamp is applied to every inserted
    row.
    """
    if not alerts:
        return
    from state.db import get_connection

    uid = _resolve_user_id(user_id)
    conn = get_connection()
    rows = [
        (a.alert_id, a.created_at, a.alert_type, a.severity, a.title, a.body,
         a.ticker, a.route_id, a.port_locode, a.value, a.threshold,
         a.change_pct, 1 if a.acknowledged else 0, uid)
        for a in alerts
    ]
    try:
        with conn:
            # INSERT OR IGNORE — dedupe by alert_id primary key.
            conn.executemany(
                """
                INSERT OR IGNORE INTO alerts
                  (alert_id, created_at, alert_type, severity, title, body,
                   ticker, route_id, port_locode, value, threshold, change_pct,
                   acknowledged, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
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


def acknowledge_alert(alert_id: str, *, user_id: Optional[str] = None) -> None:
    """Mark a single alert as acknowledged.

    Also stamps ``acknowledged_at`` with the current ISO UTC timestamp
    so the alert-analytics module can compute median time-to-ack.
    Pre-v4 rows that were acked before this column existed keep an
    empty string here and are excluded from the time-to-ack metric.

    Honours per-user scoping: when ``user_id`` resolves to a non-empty
    string, the UPDATE matches only rows the user can SEE (their own
    rows PLUS legacy rows). A user cannot ACK another user's alert by
    knowing its id — the UPDATE silently no-ops.
    """
    from state.db import get_connection
    from state.user_scope import scope_filter_sql

    uid = _resolve_user_id(user_id)
    scope_sql, scope_params = scope_filter_sql(uid)
    conn = get_connection()
    ack_ts = _now_iso()
    try:
        with conn:
            conn.execute(
                f"UPDATE alerts SET acknowledged = 1, acknowledged_at = ? "
                f"WHERE alert_id = ? {scope_sql}",
                (ack_ts, alert_id, *scope_params),
            )
    except Exception as exc:
        logger.warning(f"acknowledge_alert: SQLite update failed: {exc}")
    # Audit-log the ACK. record_audit never raises but we still wrap in
    # try/except here as belt-and-braces — this hook sits inside the
    # critical user-action path and an audit-write bug must never block
    # the ACK from completing.
    try:
        from auth.audit import record_audit
        record_audit(
            "ack_alert",
            entity_type="alert",
            entity_id=alert_id,
            detail={"acknowledged_at": ack_ts},
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
    rule-to-channel routing landed). Callers that need a normalized
    shape — e.g. ``deliver_pending_for_rule`` — should run rules
    through this helper first. ``load_rules()`` itself is intentionally
    NOT auto-normalizing so the save→load round-trip stays byte-exact
    for legacy callers that compare the loaded list against what they
    saved.

    Returns the same dict object with ``target_channels`` set to a
    list[str] (empty == 'all eligible channels', the legacy behaviour).
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
