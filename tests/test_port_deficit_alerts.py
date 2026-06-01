"""Defining-property tests for check_port_deficit_alerts."""
from __future__ import annotations

import pytest

from engine.alert_engine_v2 import ShippingAlert, check_port_deficit_alerts


# ── 1. Alert dataclass contract ───────────────────────────────────────────

def test_returns_list_of_shipping_alerts() -> None:
    alerts = check_port_deficit_alerts()
    assert isinstance(alerts, list)
    for a in alerts:
        assert isinstance(a, ShippingAlert)
        assert a.alert_type == "PORT_DEFICIT"
        assert a.severity in {"CRITICAL", "HIGH"}
        assert a.port_locode    # always populated
        assert a.title and a.body


# ── 2. Threshold gating ──────────────────────────────────────────────────

def test_alerts_only_fire_below_high_threshold() -> None:
    """The default ``high_threshold_days=-3.0`` is the lower edge — only
    ports below that should fire."""
    alerts = check_port_deficit_alerts(high_threshold_days=-3.0)
    for a in alerts:
        # value carries the supply_deficit_days that triggered the alert
        assert a.value <= -3.0


def test_critical_severity_only_fires_at_critical_threshold() -> None:
    """Severity ladder: deficit <= critical_threshold_days → CRITICAL,
    else HIGH. Pin the threshold so the ladder doesn't silently shift."""
    alerts = check_port_deficit_alerts(
        critical_threshold_days=-10.0, high_threshold_days=-3.0,
    )
    for a in alerts:
        if a.severity == "CRITICAL":
            assert a.value <= -10.0
        elif a.severity == "HIGH":
            assert -10.0 < a.value <= -3.0


def test_loose_high_threshold_fires_more_alerts_than_tight() -> None:
    """Lowering the firing bar (less negative) should produce >=
    as many alerts as a stricter bar — monotonicity check."""
    loose = check_port_deficit_alerts(high_threshold_days=0.0)
    tight = check_port_deficit_alerts(high_threshold_days=-15.0)
    assert len(loose) >= len(tight)


# ── 3. Alert body carries the rich context ────────────────────────────────

def test_body_includes_top_exposed_tickers_when_present() -> None:
    """When the port has exposed companies, their tickers must appear
    in the alert body (so on-call operators see WHO is at risk without
    re-running the supply-lines analysis)."""
    alerts = check_port_deficit_alerts()
    for a in alerts:
        if "Top exposed tickers:" not in a.body:
            # Alert body must explicitly say there's no exposure rather
            # than silently omit the section
            assert "No publicly-traded exposure mapped" in a.body


def test_body_includes_routes_clause() -> None:
    """Alert body must surface route count (or explicitly say none)."""
    alerts = check_port_deficit_alerts()
    for a in alerts:
        body_l = a.body.lower()
        assert "route" in body_l


def test_title_includes_port_name_and_locode() -> None:
    alerts = check_port_deficit_alerts()
    for a in alerts:
        assert a.port_locode in a.title


# ── 4. Container-type parameter ──────────────────────────────────────────

def test_container_type_propagates_into_body() -> None:
    """The body should reference the container type used for the scan
    so an operator triaging two alerts can tell them apart."""
    alerts = check_port_deficit_alerts(container_type="40FT_REEFER")
    for a in alerts:
        assert "40FT_REEFER" in a.body


# ── 5. Defensive failures degrade to empty list ──────────────────────────

def test_returns_empty_list_when_underlying_module_unavailable(
    monkeypatch,
) -> None:
    """If the supply-lines build raises, the alert check must return [] —
    a failing analytical module cannot crash the alert pipeline."""
    import processing.port_supply_lines as psl

    def _boom(*_a, **_kw):
        raise RuntimeError("simulated")

    monkeypatch.setattr(psl, "build_port_supply_chains", _boom)
    alerts = check_port_deficit_alerts()
    assert alerts == []


# ── 6. Generated alerts have unique alert_ids (uuid contract) ────────────

def test_alert_ids_unique_across_one_scan() -> None:
    alerts = check_port_deficit_alerts(high_threshold_days=0.0)
    ids = [a.alert_id for a in alerts]
    assert len(ids) == len(set(ids))


# ── 7. change_pct carries the breach magnitude ───────────────────────────

def test_change_pct_is_breach_magnitude_in_days() -> None:
    """The alert table's change_pct column reads the magnitude of the
    breach — for port-deficit alerts that's
    ``high_threshold_days - deficit_days``, which is always >= 0 when
    the alert fires."""
    alerts = check_port_deficit_alerts(high_threshold_days=-3.0)
    for a in alerts:
        # Alert fires only when deficit <= -3.0, so the breach magnitude
        # is at least zero.
        assert a.change_pct >= 0.0
        # And it must equal high_threshold_days - value (no rounding).
        assert abs(a.change_pct - (-3.0 - a.value)) < 1e-9
