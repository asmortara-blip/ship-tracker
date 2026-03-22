"""Comprehensive shipping alert engine v2 — detection, persistence, and acknowledgement."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, asdict
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
    """Return a sorted pandas Series of BDI values, or None."""
    try:
        import pandas as pd
        bdi_df = macro_data.get("BDI") or macro_data.get("bdi")
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
#  Persistence
# ─────────────────────────────────────────────────────────────────────────────

ALERT_FILE = Path("cache/alerts/alerts.json")
_MAX_STORED = 500


def _load_raw() -> list[dict]:
    if not ALERT_FILE.exists():
        return []
    try:
        with ALERT_FILE.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.warning(f"Could not read alerts file: {exc}")
        return []


def _save_raw(records: list[dict]) -> None:
    ALERT_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with ALERT_FILE.open("w", encoding="utf-8") as fh:
            json.dump(records, fh, indent=2, default=str)
    except Exception as exc:
        logger.warning(f"Could not write alerts file: {exc}")


def _dict_to_alert(d: dict) -> Optional[ShippingAlert]:
    try:
        return ShippingAlert(
            alert_id=d.get("alert_id", _new_id()),
            created_at=d.get("created_at", _now_iso()),
            alert_type=d.get("alert_type", "MACRO"),
            severity=d.get("severity", "LOW"),
            title=d.get("title", ""),
            body=d.get("body", ""),
            ticker=d.get("ticker", ""),
            route_id=d.get("route_id", ""),
            port_locode=d.get("port_locode", ""),
            value=float(d.get("value", 0.0)),
            threshold=float(d.get("threshold", 0.0)),
            change_pct=float(d.get("change_pct", 0.0)),
            acknowledged=bool(d.get("acknowledged", False)),
        )
    except Exception:
        return None


def save_alerts(alerts: list[ShippingAlert]) -> None:
    """Append new alerts to JSON file, max 500 stored."""
    existing = _load_raw()
    existing_ids = {r["alert_id"] for r in existing if "alert_id" in r}

    new_records = [
        asdict(a) for a in alerts
        if a.alert_id not in existing_ids
    ]
    combined = existing + new_records
    # Trim to max; keep newest (last in list after sort by created_at)
    combined.sort(key=lambda r: r.get("created_at", ""))
    if len(combined) > _MAX_STORED:
        combined = combined[-_MAX_STORED:]
    _save_raw(combined)


def load_alerts(max_age_days: int = 30) -> list[ShippingAlert]:
    """Load recent alerts from JSON file."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    records = _load_raw()
    alerts: list[ShippingAlert] = []
    for rec in records:
        if rec.get("created_at", "") < cutoff:
            continue
        alert = _dict_to_alert(rec)
        if alert is not None:
            alerts.append(alert)
    return alerts


def acknowledge_alert(alert_id: str) -> None:
    """Mark alert as acknowledged."""
    records = _load_raw()
    for rec in records:
        if rec.get("alert_id") == alert_id:
            rec["acknowledged"] = True
            break
    _save_raw(records)


def acknowledge_all() -> None:
    """Mark all alerts as acknowledged."""
    records = _load_raw()
    for rec in records:
        rec["acknowledged"] = True
    _save_raw(records)


def get_unread_count() -> int:
    """Count unacknowledged alerts."""
    records = _load_raw()
    return sum(1 for r in records if not r.get("acknowledged", False))
