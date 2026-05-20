"""Pure-function tests for engine.alert_engine.

The alert engine evaluates threshold conditions over freight rates, port
results, macro indicators and convergence insights, and emits a list of
``ShippingAlert`` objects. These tests pin:

  * the AlertConfig defaults and the ShippingAlert dataclass shape;
  * ``generate_alerts`` firing logic — RATE_SPIKE, RATE_CRASH, DEMAND_SURGE,
    CONGESTION_ALERT, MACRO_SHIFT(BDI), CONVERGENCE — each in isolation;
  * the no-false-positive contract: flat / sub-threshold inputs fire nothing;
  * severity mapping (RATE_CRASH→CRITICAL, RATE_SPIKE/DEMAND/CONGESTION→
    WARNING, CONVERGENCE→INFO) and the SEVERITY_COLORS / SEVERITY_ICONS join;
  * dict-keyed vs list-of-objects input normalisation;
  * graceful degradation on empty / None inputs;
  * ``group_alerts_by_severity`` and ``get_alert_summary`` shapes.

Port / route / insight inputs are duck-typed stubs — the engine reads every
attribute via ``getattr``. Freight history is a synthetic DataFrame. No
Streamlit, no live feed. ``generate_alerts`` stamps a fresh uuid4 alert_id
per call, so determinism is asserted only on counts and types.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from engine.alert_engine import (
    SEVERITY_COLORS,
    SEVERITY_ICONS,
    AlertConfig,
    ShippingAlert,
    generate_alerts,
    get_alert_summary,
    group_alerts_by_severity,
)


# ── Duck-typed input stubs ──────────────────────────────────────────────────


class _Port:
    """Minimal stand-in for a PortDemandResult."""

    def __init__(self, locode, name, demand_score=None, congestion_score=None):
        self.locode = locode
        self.name = name
        if demand_score is not None:
            self.demand_score = demand_score
        if congestion_score is not None:
            self.congestion_score = congestion_score


class _Route:
    """Minimal stand-in for a RouteOpportunity."""

    def __init__(self, route_id, route_name):
        self.route_id = route_id
        self.route_name = route_name


class _Insight:
    """Minimal stand-in for an engine.insight.Insight."""

    def __init__(self, category, score, title="Signal", detail="", ports=None):
        self.category = category
        self.score = score
        self.title = title
        self.detail = detail
        self.ports_involved = ports or []
        self.routes_involved = []


# ── Freight history builders ────────────────────────────────────────────────


def _rate_history(rates: list[float], route_id: str = "r") -> pd.DataFrame:
    """A freight DataFrame whose first→last span drives the 30-day change."""
    n = len(rates)
    base = date.today() - timedelta(days=n)
    dates = pd.to_datetime([base + timedelta(days=i) for i in range(n)])
    return pd.DataFrame(
        {"date": dates, "route_id": route_id, "rate_usd_per_feu": rates}
    )


def _flat_freight() -> dict[str, pd.DataFrame]:
    """A perfectly flat 60-day series — must not trigger spike or crash."""
    return {"transpacific_eb": _rate_history([2000.0] * 60, "transpacific_eb")}


# ── AlertConfig defaults ────────────────────────────────────────────────────


def test_alert_config_defaults() -> None:
    cfg = AlertConfig()
    assert cfg.rate_spike_pct == 0.15
    assert cfg.rate_crash_pct == -0.20
    assert cfg.demand_surge_threshold == 0.75
    assert cfg.congestion_threshold == 0.80
    assert cfg.macro_bdi_drop_pct == -0.25
    assert cfg.insight_convergence_threshold == 0.75


# ── No false positives ──────────────────────────────────────────────────────


def test_no_inputs_produce_no_alerts() -> None:
    """Empty/None everywhere → an empty alert list, no crash."""
    alerts = generate_alerts([], [], {}, {}, [])
    assert alerts == []


def test_none_inputs_are_tolerated() -> None:
    """The engine guards every container with `or {}` / `or []`."""
    alerts = generate_alerts(None, None, None, None, None)
    assert isinstance(alerts, list)


def test_flat_rates_and_calm_ports_fire_nothing() -> None:
    """Sub-threshold inputs across the board produce zero alerts."""
    ports = [_Port("USLAX", "Los Angeles", demand_score=0.40, congestion_score=0.30)]
    alerts = generate_alerts(ports, [], _flat_freight(), {}, [])
    assert alerts == []


# ── RATE_SPIKE ──────────────────────────────────────────────────────────────


def test_rate_spike_fires_on_large_increase() -> None:
    """A +40% move clears the 15% spike threshold and fires a WARNING."""
    freight = {"transpacific_eb": _rate_history([2000.0, 2800.0], "transpacific_eb")}
    routes = [_Route("transpacific_eb", "Trans-Pacific EB")]
    alerts = generate_alerts([], routes, freight, {}, [])
    spikes = [a for a in alerts if a.alert_type == "RATE_SPIKE"]
    assert len(spikes) == 1
    spike = spikes[0]
    assert spike.severity == "WARNING"
    assert spike.affected_entity == "transpacific_eb"
    assert spike.entity_name == "Trans-Pacific EB"
    assert spike.current_value == pytest.approx(2800.0)


def test_rate_spike_just_below_threshold_does_not_fire() -> None:
    """A +14% move stays under the 15% threshold — no spike."""
    freight = {"r": _rate_history([2000.0, 2280.0], "r")}
    alerts = generate_alerts([], [], freight, {}, [])
    assert not [a for a in alerts if a.alert_type == "RATE_SPIKE"]


# ── RATE_CRASH ──────────────────────────────────────────────────────────────


def test_rate_crash_fires_on_large_drop_as_critical() -> None:
    """A -40% move clears the -20% crash threshold and fires CRITICAL."""
    freight = {"asia_europe": _rate_history([2000.0, 1200.0], "asia_europe")}
    alerts = generate_alerts([], [], freight, {}, [])
    crashes = [a for a in alerts if a.alert_type == "RATE_CRASH"]
    assert len(crashes) == 1
    assert crashes[0].severity == "CRITICAL"
    assert crashes[0].affected_entity == "asia_europe"


def test_rate_crash_just_above_threshold_does_not_fire() -> None:
    """A -19% move stays inside the -20% threshold — no crash."""
    freight = {"r": _rate_history([2000.0, 1620.0], "r")}
    alerts = generate_alerts([], [], freight, {}, [])
    assert not [a for a in alerts if a.alert_type == "RATE_CRASH"]


# ── DEMAND_SURGE ────────────────────────────────────────────────────────────


def test_demand_surge_fires_above_threshold() -> None:
    ports = [_Port("USLAX", "Los Angeles", demand_score=0.92)]
    alerts = generate_alerts(ports, [], {}, {}, [])
    surges = [a for a in alerts if a.alert_type == "DEMAND_SURGE"]
    assert len(surges) == 1
    assert surges[0].severity == "WARNING"
    assert surges[0].affected_entity == "USLAX"
    assert surges[0].current_value == pytest.approx(0.92)


def test_demand_surge_does_not_fire_at_threshold() -> None:
    """Exactly at 0.75 is not strictly above the threshold — no alert."""
    ports = [_Port("USLAX", "Los Angeles", demand_score=0.75)]
    alerts = generate_alerts(ports, [], {}, {}, [])
    assert not [a for a in alerts if a.alert_type == "DEMAND_SURGE"]


def test_port_without_demand_score_is_skipped() -> None:
    """A port stub lacking demand_score must not raise — it is simply skipped."""
    ports = [_Port("USLAX", "Los Angeles")]  # no demand/congestion attrs
    alerts = generate_alerts(ports, [], {}, {}, [])
    assert alerts == []


# ── CONGESTION_ALERT ────────────────────────────────────────────────────────


def test_congestion_alert_fires_above_threshold() -> None:
    ports = [_Port("CNSHA", "Shanghai", congestion_score=0.95)]
    alerts = generate_alerts(ports, [], {}, {}, [])
    congestion = [a for a in alerts if a.alert_type == "CONGESTION_ALERT"]
    assert len(congestion) == 1
    assert congestion[0].severity == "WARNING"
    assert congestion[0].affected_entity == "CNSHA"


def test_congestion_alert_does_not_fire_below_threshold() -> None:
    ports = [_Port("CNSHA", "Shanghai", congestion_score=0.50)]
    alerts = generate_alerts(ports, [], {}, {}, [])
    assert not [a for a in alerts if a.alert_type == "CONGESTION_ALERT"]


# ── MACRO_SHIFT (BDI) ───────────────────────────────────────────────────────


def _bdi_frame(values: list[float]) -> pd.DataFrame:
    n = len(values)
    base = date.today() - timedelta(days=n)
    dates = pd.to_datetime([base + timedelta(days=i) for i in range(n)])
    return pd.DataFrame({"date": dates, "value": values})


# generate_alerts resolves the BDI frame via an explicit None-check chain
# over the FRED canonical key ("BDIY") first, then "BDI" / "bdi" as legacy
# aliases — never `a or b` on a DataFrame (which invokes DataFrame.__bool__
# and raises ValueError).


def test_macro_shift_fires_on_steep_bdi_decline() -> None:
    """A 40-point→far-lower BDI slide over 31+ obs fires a CRITICAL macro alert."""
    values = [2000.0] * 40 + [1100.0]  # iloc[-31] is 2000, iloc[-1] is 1100 → -45%
    macro = {"bdi": _bdi_frame(values)}
    alerts = generate_alerts([], [], {}, macro, [])
    macro_alerts = [a for a in alerts if a.alert_type == "MACRO_SHIFT"]
    assert len(macro_alerts) == 1
    assert macro_alerts[0].severity == "CRITICAL"
    assert macro_alerts[0].affected_entity == "MACRO_BDI"


def test_macro_shift_does_not_fire_on_stable_bdi() -> None:
    macro = {"bdi": _bdi_frame([2000.0] * 60)}
    alerts = generate_alerts([], [], {}, macro, [])
    assert not [a for a in alerts if a.alert_type == "MACRO_SHIFT"]


def test_macro_shift_skipped_when_history_too_short() -> None:
    """Fewer than 31 BDI observations → the macro branch is skipped, no crash."""
    macro = {"bdi": _bdi_frame([2000.0, 1000.0])}  # huge drop but only 2 obs
    alerts = generate_alerts([], [], {}, macro, [])
    assert not [a for a in alerts if a.alert_type == "MACRO_SHIFT"]


def test_macro_shift_fires_with_uppercase_BDI_key() -> None:
    """The uppercase 'BDI' key resolves cleanly (no DataFrame.__bool__ crash)
    and the macro alert fires identically to the lowercase path."""
    macro = {"BDI": _bdi_frame([2000.0] * 40 + [1100.0])}
    alerts = generate_alerts([], [], {}, macro, [])
    macro_alerts = [a for a in alerts if a.alert_type == "MACRO_SHIFT"]
    assert len(macro_alerts) == 1
    assert macro_alerts[0].severity == "CRITICAL"


def test_macro_shift_fires_with_canonical_BDIY_key() -> None:
    """The canonical FRED key 'BDIY' is the live-data path; the alert must
    fire from it (previously it was silently ignored)."""
    macro = {"BDIY": _bdi_frame([2000.0] * 40 + [1100.0])}
    alerts = generate_alerts([], [], {}, macro, [])
    macro_alerts = [a for a in alerts if a.alert_type == "MACRO_SHIFT"]
    assert len(macro_alerts) == 1
    assert macro_alerts[0].severity == "CRITICAL"


# ── CONVERGENCE insights ────────────────────────────────────────────────────


def test_convergence_insight_fires_info_alert() -> None:
    insights = [_Insight("CONVERGENCE", 0.88, title="Bull stack", ports=["USLAX"])]
    alerts = generate_alerts([], [], {}, {}, insights)
    conv = [a for a in alerts if a.alert_type == "CONVERGENCE"]
    assert len(conv) == 1
    assert conv[0].severity == "INFO"
    assert conv[0].affected_entity == "USLAX"


def test_convergence_below_threshold_does_not_fire() -> None:
    insights = [_Insight("CONVERGENCE", 0.60)]
    alerts = generate_alerts([], [], {}, {}, insights)
    assert not [a for a in alerts if a.alert_type == "CONVERGENCE"]


def test_non_convergence_insight_is_ignored() -> None:
    """A high-score insight in another category does not produce a CONVERGENCE alert."""
    insights = [_Insight("MACRO", 0.95)]
    alerts = generate_alerts([], [], {}, {}, insights)
    assert not [a for a in alerts if a.alert_type == "CONVERGENCE"]


# ── ShippingAlert dataclass shape & severity join ───────────────────────────


def test_fired_alert_has_full_schema_and_consistent_palette() -> None:
    """Every fired alert is a well-formed ShippingAlert with a matching color/icon."""
    freight = {"transpacific_eb": _rate_history([2000.0, 2800.0], "transpacific_eb")}
    ports = [
        _Port("USLAX", "Los Angeles", demand_score=0.90),
        _Port("CNSHA", "Shanghai", congestion_score=0.95),
    ]
    alerts = generate_alerts(ports, [], freight, {}, [])
    assert len(alerts) >= 3
    for a in alerts:
        assert isinstance(a, ShippingAlert)
        assert a.alert_id and len(a.alert_id) == 8
        assert a.severity in {"INFO", "WARNING", "CRITICAL"}
        assert a.title and a.message and a.suggested_action
        assert a.triggered_at  # ISO timestamp string
        assert a.color == SEVERITY_COLORS[a.severity]
        assert a.icon == SEVERITY_ICONS[a.severity]
        assert isinstance(a.current_value, float)
        assert isinstance(a.pct_deviation, float)


# ── Input normalisation: dict vs list ───────────────────────────────────────


def test_dict_keyed_ports_are_normalised_like_a_list() -> None:
    """Passing port_results as a dict-keyed-by-locode behaves like a list."""
    port = _Port("USLAX", "Los Angeles", demand_score=0.90)
    as_list = generate_alerts([port], [], {}, {}, [])
    as_dict = generate_alerts({"USLAX": port}, [], {}, {}, [])
    assert len(as_list) == len(as_dict) == 1
    assert as_dict[0].alert_type == "DEMAND_SURGE"


def test_dict_keyed_routes_supply_human_readable_names() -> None:
    """A dict-keyed route_results still resolves the route name on a RATE_SPIKE."""
    freight = {"transpacific_eb": _rate_history([2000.0, 2800.0], "transpacific_eb")}
    route = _Route("transpacific_eb", "Trans-Pacific EB")
    alerts = generate_alerts([], {"transpacific_eb": route}, freight, {}, [])
    spikes = [a for a in alerts if a.alert_type == "RATE_SPIKE"]
    assert len(spikes) == 1
    assert spikes[0].entity_name == "Trans-Pacific EB"


# ── Determinism (counts / types, not the random alert_id) ───────────────────


def test_alert_counts_are_deterministic_across_calls() -> None:
    """Identical inputs yield the same alert types in the same order."""
    freight = {"asia_europe": _rate_history([2000.0, 1100.0], "asia_europe")}
    ports = [_Port("CNSHA", "Shanghai", congestion_score=0.95)]
    a = generate_alerts(ports, [], freight, {}, [])
    b = generate_alerts(ports, [], freight, {}, [])
    assert [x.alert_type for x in a] == [x.alert_type for x in b]
    assert [x.severity for x in a] == [x.severity for x in b]


# ── group_alerts_by_severity ────────────────────────────────────────────────


def test_group_alerts_by_severity_buckets_and_orders() -> None:
    """Grouping returns CRITICAL/WARNING/INFO buckets covering every alert."""
    freight = {
        "transpacific_eb": _rate_history([2000.0, 2800.0], "transpacific_eb"),  # spike→WARNING
        "asia_europe": _rate_history([2000.0, 1100.0], "asia_europe"),          # crash→CRITICAL
    }
    insights = [_Insight("CONVERGENCE", 0.90, ports=["USLAX"])]                 # →INFO
    alerts = generate_alerts([], [], freight, {}, insights)
    groups = group_alerts_by_severity(alerts)
    assert list(groups.keys())[:3] == ["CRITICAL", "WARNING", "INFO"]
    assert sum(len(v) for v in groups.values()) == len(alerts)
    assert all(a.severity == "CRITICAL" for a in groups["CRITICAL"])
    assert all(a.severity == "WARNING" for a in groups["WARNING"])
    assert all(a.severity == "INFO" for a in groups["INFO"])


def test_group_alerts_handles_empty_list() -> None:
    groups = group_alerts_by_severity([])
    assert groups == {"CRITICAL": [], "WARNING": [], "INFO": []}


# ── get_alert_summary ───────────────────────────────────────────────────────


def test_alert_summary_schema_and_counts() -> None:
    freight = {
        "transpacific_eb": _rate_history([2000.0, 2800.0], "transpacific_eb"),
        "asia_europe": _rate_history([2000.0, 1100.0], "asia_europe"),
    }
    alerts = generate_alerts([], [], freight, {}, [])
    summary = get_alert_summary(alerts)
    assert set(summary) == {
        "total", "critical", "warning", "info", "top_alert", "last_checked",
    }
    assert summary["total"] == len(alerts)
    assert summary["critical"] + summary["warning"] + summary["info"] == summary["total"]
    assert summary["last_checked"]


def test_alert_summary_top_alert_prefers_critical() -> None:
    """The summary's top_alert is the first CRITICAL when one exists."""
    freight = {
        "transpacific_eb": _rate_history([2000.0, 2800.0], "transpacific_eb"),  # WARNING
        "asia_europe": _rate_history([2000.0, 1100.0], "asia_europe"),          # CRITICAL
    }
    summary = get_alert_summary(generate_alerts([], [], freight, {}, []))
    assert summary["top_alert"] is not None
    assert summary["top_alert"].severity == "CRITICAL"


def test_alert_summary_empty_list_has_no_top_alert() -> None:
    summary = get_alert_summary([])
    assert summary["total"] == 0
    assert summary["critical"] == summary["warning"] == summary["info"] == 0
    assert summary["top_alert"] is None
