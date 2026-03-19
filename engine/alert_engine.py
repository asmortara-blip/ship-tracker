"""Smart alert generation and threshold monitoring system for shipping analytics."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.insight import Insight


# ── Severity palette ──────────────────────────────────────────────────────────

SEVERITY_COLORS: dict[str, str] = {
    "INFO":     "#3b82f6",
    "WARNING":  "#f59e0b",
    "CRITICAL": "#ef4444",
}

SEVERITY_ICONS: dict[str, str] = {
    "INFO":     "ℹ️",
    "WARNING":  "⚠️",
    "CRITICAL": "🚨",
}

ALERT_TYPES = [
    "RATE_SPIKE",
    "RATE_CRASH",
    "DEMAND_SURGE",
    "CONGESTION_ALERT",
    "MACRO_SHIFT",
    "CONVERGENCE",
    "BREAKOUT",
    "CUSTOM",
]


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class AlertConfig:
    """Thresholds that control when alerts fire."""
    rate_spike_pct: float = 0.15           # Alert if rate spikes >15% in 30d
    rate_crash_pct: float = -0.20          # Alert if rate crashes >20% in 30d
    demand_surge_threshold: float = 0.75   # Alert if demand score > 75%
    congestion_threshold: float = 0.80     # Alert if congestion score > 80%
    macro_pmi_low: float = 47.0            # Alert if PMI drops below this
    macro_bdi_drop_pct: float = -0.25      # Alert if BDI drops >25% in 30d
    insight_convergence_threshold: float = 0.75  # Alert if convergence score > 75%


@dataclass
class ShippingAlert:
    """A single actionable shipping alert."""
    alert_id: str           # Short uuid4 identifier
    alert_type: str         # RATE_SPIKE | RATE_CRASH | DEMAND_SURGE | CONGESTION_ALERT |
                            #   MACRO_SHIFT | CONVERGENCE | BREAKOUT | CUSTOM
    severity: str           # INFO | WARNING | CRITICAL
    title: str
    message: str
    affected_entity: str    # route_id or port LOCODE
    entity_name: str
    current_value: float
    threshold_value: float
    pct_deviation: float    # How far current_value is from threshold_value (signed %)
    triggered_at: str       # ISO 8601 UTC datetime
    suggested_action: str
    color: str              # Hex color based on severity
    icon: str               # Emoji based on severity


# ── Internal helpers ──────────────────────────────────────────────────────────

def _short_id() -> str:
    return str(uuid.uuid4())[:8]


def _make_alert(
    alert_type: str,
    severity: str,
    title: str,
    message: str,
    affected_entity: str,
    entity_name: str,
    current_value: float,
    threshold_value: float,
    pct_deviation: float,
    suggested_action: str,
) -> ShippingAlert:
    from utils.helpers import now_iso
    return ShippingAlert(
        alert_id=_short_id(),
        alert_type=alert_type,
        severity=severity,
        title=title,
        message=message,
        affected_entity=affected_entity,
        entity_name=entity_name,
        current_value=current_value,
        threshold_value=threshold_value,
        pct_deviation=pct_deviation,
        triggered_at=now_iso(),
        suggested_action=suggested_action,
        color=SEVERITY_COLORS[severity],
        icon=SEVERITY_ICONS[severity],
    )


def _rate_30d_change(df) -> float | None:
    """Compute 30-day percentage change in rate_usd_per_feu from a DataFrame."""
    if df is None or df.empty or "rate_usd_per_feu" not in df.columns:
        return None
    df = df.sort_values("date")
    rates = df["rate_usd_per_feu"].dropna()
    if len(rates) < 31:
        if len(rates) < 2:
            return None
        current = float(rates.iloc[-1])
        past = float(rates.iloc[0])
    else:
        current = float(rates.iloc[-1])
        past = float(rates.iloc[-31])
    if past == 0:
        return None
    return (current - past) / past


# ── Core alert generation ─────────────────────────────────────────────────────

def generate_alerts(
    port_results: list | dict,
    route_results: list | dict,
    freight_data: dict,
    macro_data: dict,
    insights: list,
    config: AlertConfig | None = None,
) -> list[ShippingAlert]:
    """Evaluate all alert conditions and return a list of fired ShippingAlert objects.

    Parameters
    ----------
    port_results:
        Port analysis results — supports both list-of-objects and dict-keyed-by-locode.
    route_results:
        Route analysis results — supports both list-of-objects and dict-keyed-by-id.
    freight_data:
        Mapping of route_id -> DataFrame with columns [date, rate_usd_per_feu].
    macro_data:
        Dict of macro indicators, e.g. {"BDI": DataFrame, "PMI": float, ...}.
    insights:
        List of Insight objects produced by the decision engine.
    config:
        AlertConfig instance; uses defaults if None.
    """
    if config is None:
        config = AlertConfig()

    alerts: list[ShippingAlert] = []

    # ── Normalise port_results to an iterable of objects ──────────────────────
    ports_iter: list = (
        list(port_results.values()) if isinstance(port_results, dict) else (port_results or [])
    )

    # ── Normalise route_results to an iterable of objects ─────────────────────
    routes_iter: list = (
        list(route_results.values()) if isinstance(route_results, dict) else (route_results or [])
    )

    # ── 1. RATE_SPIKE & RATE_CRASH ─────────────────────────────────────────────
    for route_id, df in (freight_data or {}).items():
        chg = _rate_30d_change(df)
        if chg is None:
            continue

        # Find a human-readable route name from route_results if available
        route_name = route_id
        for r in routes_iter:
            rid = getattr(r, "route_id", getattr(r, "id", None))
            if rid == route_id:
                route_name = getattr(r, "route_name", getattr(r, "name", route_id))
                break

        current_rate: float = 0.0
        past_rate: float = 0.0
        df_sorted = df.sort_values("date") if not df.empty else df
        if "rate_usd_per_feu" in df.columns and len(df_sorted) >= 1:
            rates = df_sorted["rate_usd_per_feu"].dropna()
            current_rate = float(rates.iloc[-1]) if len(rates) >= 1 else 0.0
            past_rate = float(rates.iloc[-31]) if len(rates) >= 31 else float(rates.iloc[0])

        if chg > config.rate_spike_pct:
            threshold_rate = past_rate * (1 + config.rate_spike_pct)
            pct_dev = ((chg - config.rate_spike_pct) / config.rate_spike_pct) * 100
            alerts.append(_make_alert(
                alert_type="RATE_SPIKE",
                severity="WARNING",
                title=f"Rate Spike: {route_name}",
                message=(
                    f"Freight rate on {route_name} has risen {chg:.1%} over the past 30 days, "
                    f"exceeding the {config.rate_spike_pct:.0%} spike threshold. "
                    f"Current rate: ${current_rate:,.0f}/FEU."
                ),
                affected_entity=route_id,
                entity_name=route_name,
                current_value=current_rate,
                threshold_value=threshold_rate,
                pct_deviation=pct_dev,
                suggested_action="Consider booking forward capacity now before rates rise further.",
            ))

        elif chg < config.rate_crash_pct:
            threshold_rate = past_rate * (1 + config.rate_crash_pct)
            pct_dev = ((chg - config.rate_crash_pct) / abs(config.rate_crash_pct)) * 100
            alerts.append(_make_alert(
                alert_type="RATE_CRASH",
                severity="CRITICAL",
                title=f"Rate Crash: {route_name}",
                message=(
                    f"Freight rate on {route_name} has fallen {abs(chg):.1%} over the past 30 days, "
                    f"exceeding the {abs(config.rate_crash_pct):.0%} crash threshold. "
                    f"Current rate: ${current_rate:,.0f}/FEU."
                ),
                affected_entity=route_id,
                entity_name=route_name,
                current_value=current_rate,
                threshold_value=threshold_rate,
                pct_deviation=pct_dev,
                suggested_action="Spot market favorable — delay forward bookings if possible.",
            ))

    # ── 2. DEMAND_SURGE ────────────────────────────────────────────────────────
    for port in ports_iter:
        demand_score = getattr(port, "demand_score", None)
        if demand_score is None:
            continue
        if demand_score > config.demand_surge_threshold:
            locode = getattr(port, "locode", getattr(port, "port_id", "UNKNOWN"))
            name = getattr(port, "name", getattr(port, "port_name", locode))
            pct_dev = ((demand_score - config.demand_surge_threshold)
                       / config.demand_surge_threshold) * 100
            alerts.append(_make_alert(
                alert_type="DEMAND_SURGE",
                severity="WARNING",
                title=f"Demand Surge: {name}",
                message=(
                    f"Port {name} ({locode}) is showing a demand score of {demand_score:.1%}, "
                    f"above the {config.demand_surge_threshold:.0%} surge threshold. "
                    f"Elevated vessel traffic and booking pressure expected."
                ),
                affected_entity=locode,
                entity_name=name,
                current_value=demand_score,
                threshold_value=config.demand_surge_threshold,
                pct_deviation=pct_dev,
                suggested_action=(
                    f"High demand at {name} — expect longer booking lead times and potential congestion."
                ),
            ))

    # ── 3. CONGESTION_ALERT ────────────────────────────────────────────────────
    for port in ports_iter:
        congestion_score = getattr(port, "congestion_score", None)
        if congestion_score is None:
            continue
        if congestion_score > config.congestion_threshold:
            locode = getattr(port, "locode", getattr(port, "port_id", "UNKNOWN"))
            name = getattr(port, "name", getattr(port, "port_name", locode))
            pct_dev = ((congestion_score - config.congestion_threshold)
                       / config.congestion_threshold) * 100
            alerts.append(_make_alert(
                alert_type="CONGESTION_ALERT",
                severity="WARNING",
                title=f"Congestion Alert: {name}",
                message=(
                    f"Port {name} ({locode}) is reporting a congestion score of "
                    f"{congestion_score:.1%}, above the {config.congestion_threshold:.0%} threshold. "
                    f"Vessel dwell times and port fees may increase."
                ),
                affected_entity=locode,
                entity_name=name,
                current_value=congestion_score,
                threshold_value=config.congestion_threshold,
                pct_deviation=pct_dev,
                suggested_action=(
                    f"Consider re-routing away from {name} or building extra transit buffer into schedules."
                ),
            ))

    # ── 4. MACRO_SHIFT — BDI ──────────────────────────────────────────────────
    bdi_df = (macro_data or {}).get("BDI") or (macro_data or {}).get("bdi")
    if bdi_df is not None and not getattr(bdi_df, "empty", True):
        try:
            import pandas as pd
            if hasattr(bdi_df, "sort_values"):
                date_col = "date" if "date" in bdi_df.columns else bdi_df.columns[0]
                val_col = (
                    "value" if "value" in bdi_df.columns
                    else [c for c in bdi_df.columns if c != date_col][0]
                )
                bdi_sorted = bdi_df.sort_values(date_col)
                bdi_vals = bdi_sorted[val_col].dropna()
                if len(bdi_vals) >= 31:
                    bdi_current = float(bdi_vals.iloc[-1])
                    bdi_30d_ago = float(bdi_vals.iloc[-31])
                    if bdi_30d_ago != 0:
                        bdi_chg = (bdi_current - bdi_30d_ago) / bdi_30d_ago
                        if bdi_chg < config.macro_bdi_drop_pct:
                            threshold_bdi = bdi_30d_ago * (1 + config.macro_bdi_drop_pct)
                            pct_dev = ((bdi_chg - config.macro_bdi_drop_pct)
                                       / abs(config.macro_bdi_drop_pct)) * 100
                            alerts.append(_make_alert(
                                alert_type="MACRO_SHIFT",
                                severity="CRITICAL",
                                title="Macro Shift: BDI Steep Decline",
                                message=(
                                    f"The Baltic Dry Index has fallen {abs(bdi_chg):.1%} over the past "
                                    f"30 days (current: {bdi_current:,.0f}), exceeding the "
                                    f"{abs(config.macro_bdi_drop_pct):.0%} macro-shift threshold. "
                                    f"Broad shipping demand weakness is signalled."
                                ),
                                affected_entity="MACRO_BDI",
                                entity_name="Baltic Dry Index",
                                current_value=bdi_current,
                                threshold_value=threshold_bdi,
                                pct_deviation=pct_dev,
                                suggested_action=(
                                    "Macro headwinds accelerating — reduce speculative freight exposure "
                                    "and hedge forward positions."
                                ),
                            ))
        except Exception:
            pass  # Non-fatal; macro data may be absent or malformed

    # ── 5. CONVERGENCE insights ────────────────────────────────────────────────
    for insight in (insights or []):
        category = getattr(insight, "category", "")
        score = getattr(insight, "score", 0.0)
        if category == "CONVERGENCE" and score > config.insight_convergence_threshold:
            ports_inv = getattr(insight, "ports_involved", [])
            routes_inv = getattr(insight, "routes_involved", [])
            entity = (ports_inv[0] if ports_inv else routes_inv[0] if routes_inv else "GLOBAL")
            entity_name = entity
            title_text = getattr(insight, "title", "Convergence Signal")
            pct_dev = ((score - config.insight_convergence_threshold)
                       / config.insight_convergence_threshold) * 100
            alerts.append(_make_alert(
                alert_type="CONVERGENCE",
                severity="INFO",
                title=f"Convergence Signal: {title_text}",
                message=(
                    f"Multiple data signals are aligning bullishly with a convergence score of "
                    f"{score:.1%} (threshold {config.insight_convergence_threshold:.0%}). "
                    f"{getattr(insight, 'detail', '')}"
                ),
                affected_entity=entity,
                entity_name=entity_name,
                current_value=score,
                threshold_value=config.insight_convergence_threshold,
                pct_deviation=pct_dev,
                suggested_action=(
                    "Multiple signals align bullishly — consider increasing shipping allocation."
                ),
            ))

    # ── 6. BREAKOUT — via freight_volatility ──────────────────────────────────
    try:
        from processing.freight_volatility import (
            analyze_all_routes_volatility,
            get_breakout_alerts,
        )
        vol_reports = analyze_all_routes_volatility(freight_data or {})
        breakout_reports = get_breakout_alerts(vol_reports)
        for rep in breakout_reports:
            zscore = abs(getattr(rep, "zscore_from_mean", 0.0))
            current_rate_br = 0.0
            df_br = (freight_data or {}).get(rep.route_id)
            if df_br is not None and not df_br.empty and "rate_usd_per_feu" in df_br.columns:
                current_rate_br = float(df_br.sort_values("date")["rate_usd_per_feu"].dropna().iloc[-1])
            pct_dev_br = (zscore / 2.0) * 100  # Normalise z-score as deviation %
            alerts.append(_make_alert(
                alert_type="BREAKOUT",
                severity="WARNING",
                title=f"Breakout Detected: {rep.route_name}",
                message=(
                    f"Route {rep.route_name} is in a BREAKOUT regime with a z-score of "
                    f"{rep.zscore_from_mean:+.2f} vs its 90-day mean. "
                    f"Signal strength: {rep.signal_strength:.1%}. "
                    f"Mean-reversion signal: {rep.mean_reversion_signal}."
                ),
                affected_entity=rep.route_id,
                entity_name=rep.route_name,
                current_value=current_rate_br,
                threshold_value=0.0,
                pct_deviation=pct_dev_br,
                suggested_action=(
                    "Unusual rate movement detected — review position sizing and "
                    "monitor for reversion or trend continuation."
                ),
            ))
    except Exception:
        pass  # freight_volatility is optional; skip silently

    return alerts


# ── Grouping & summary helpers ────────────────────────────────────────────────

def group_alerts_by_severity(alerts: list[ShippingAlert]) -> dict[str, list[ShippingAlert]]:
    """Return alerts grouped by severity level, ordered CRITICAL → WARNING → INFO."""
    groups: dict[str, list[ShippingAlert]] = {"CRITICAL": [], "WARNING": [], "INFO": []}
    for alert in alerts:
        bucket = groups.get(alert.severity)
        if bucket is not None:
            bucket.append(alert)
        else:
            groups.setdefault(alert.severity, []).append(alert)
    return groups


def get_alert_summary(alerts: list[ShippingAlert]) -> dict:
    """Return a high-level summary dict for the alert list."""
    from utils.helpers import now_iso

    critical = [a for a in alerts if a.severity == "CRITICAL"]
    warning  = [a for a in alerts if a.severity == "WARNING"]
    info     = [a for a in alerts if a.severity == "INFO"]

    # Top alert: first CRITICAL, else first WARNING, else first INFO
    top: ShippingAlert | None = None
    for bucket in (critical, warning, info):
        if bucket:
            top = bucket[0]
            break

    return {
        "total":       len(alerts),
        "critical":    len(critical),
        "warning":     len(warning),
        "info":        len(info),
        "top_alert":   top,
        "last_checked": now_iso(),
    }
