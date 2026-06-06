"""Tests for the live canal→chokepoint overlay (rec R007).

The overlay mutates the module-level CHOKEPOINTS registry, so every test that
exercises a REAL (non-synthetic) overlay uses ``restore_chokepoints`` to snapshot
+ restore the canal entries — the overlay must never leak across tests.
"""
from __future__ import annotations

import dataclasses

import pytest

from data.canal_feed import CanalStats
from processing.canal_chokepoint_sync import (
    apply_live_canal_state,
    map_canal_to_chokepoint,
)
from processing.chokepoint_analyzer import CHOKEPOINTS


def _stats(canal: str, status: str, *, util: float = 50.0, transits: int = 40,
           synth: bool = False) -> CanalStats:
    return CanalStats(
        canal=canal, northbound_wait_days=1.0, southbound_wait_days=1.0,
        daily_transits=transits, capacity_utilization_pct=util, water_level_m=0.0,
        status=status, restrictions="", source_url="", fetched_at="",
        is_synthetic=synth,
    )


@pytest.fixture
def restore_chokepoints():
    """Snapshot + restore the canal registry entries so the overlay never leaks."""
    snap = {k: dataclasses.replace(CHOKEPOINTS[k]) for k in ("suez", "panama")}
    yield
    for k, v in snap.items():
        CHOKEPOINTS[k] = v


# ── mapping ──────────────────────────────────────────────────────────────────

def test_map_disrupted_is_critical_conflict() -> None:
    m = map_canal_to_chokepoint(_stats("Suez", "Disrupted", transits=20))
    assert m["current_risk_level"] == "CRITICAL"
    assert m["current_disruption_type"] == "ACTIVE_CONFLICT"
    assert m["daily_vessels"] == 20


def test_map_restricted_is_high() -> None:
    assert map_canal_to_chokepoint(_stats("Suez", "Restricted"))["current_risk_level"] == "HIGH"


def test_map_normal_high_util_is_congestion() -> None:
    m = map_canal_to_chokepoint(_stats("Panama", "Normal", util=95.0))
    assert m["current_risk_level"] == "MODERATE"
    assert m["current_disruption_type"] == "CONGESTION"


def test_map_normal_low_util_is_low_none() -> None:
    m = map_canal_to_chokepoint(_stats("Panama", "Normal", util=70.0))
    assert m["current_risk_level"] == "LOW" and m["current_disruption_type"] == "NONE"


def test_map_panama_stress_cause_is_weather() -> None:
    assert map_canal_to_chokepoint(_stats("Panama", "Disrupted"))["current_disruption_type"] == "WEATHER"


# ── overlay (real-only; synthetic skipped) ───────────────────────────────────

def test_overlay_skips_synthetic_leaves_baseline(restore_chokepoints) -> None:
    before = CHOKEPOINTS["suez"].current_risk_level
    marker = apply_live_canal_state([_stats("Suez", "Disrupted", synth=True)])
    assert marker["suez"]["realness"] == "modeled"
    assert CHOKEPOINTS["suez"].current_risk_level == before     # untouched


def test_overlay_applies_real(restore_chokepoints) -> None:
    marker = apply_live_canal_state([_stats("Suez", "Disrupted", transits=15, synth=False)])
    assert marker["suez"]["realness"] == "live"
    assert CHOKEPOINTS["suez"].current_risk_level == "CRITICAL"
    assert CHOKEPOINTS["suez"].daily_vessels == 15


def test_overlay_is_idempotent(restore_chokepoints) -> None:
    s = _stats("Panama", "Restricted", transits=30, synth=False)
    apply_live_canal_state([s])
    first = dataclasses.replace(CHOKEPOINTS["panama"])
    apply_live_canal_state([s])
    assert CHOKEPOINTS["panama"] == first


def test_overlay_ignores_unknown_canal(restore_chokepoints) -> None:
    assert apply_live_canal_state([_stats("Bosphorus", "Disrupted", synth=False)]) == {}


# ── SSI integration ──────────────────────────────────────────────────────────

def test_ssi_consumes_real_canal_state(restore_chokepoints) -> None:
    from processing.shipping_stress_index import compute_shipping_stress
    rep = compute_shipping_stress(
        {}, {}, [], [],
        canal_stats=[_stats("Suez", "Disrupted", transits=10, synth=False)],
    )
    assert rep.canal_data_realness.get("suez", {}).get("realness") == "live"
    assert CHOKEPOINTS["suez"].current_risk_level == "CRITICAL"


def test_ssi_default_is_no_overlay() -> None:
    # No canal_stats → no overlay, empty realness marker (the whole test suite
    # relies on this: the SSI baseline stays deterministic).
    from processing.shipping_stress_index import compute_shipping_stress
    rep = compute_shipping_stress({}, {}, [], [])
    assert rep.canal_data_realness == {}


# ── scheduler job ────────────────────────────────────────────────────────────

def test_run_canal_sync_job_overlays_real_only(monkeypatch, restore_chokepoints) -> None:
    import worker.scheduler as sched
    monkeypatch.setattr(
        "data.canal_feed.fetch_suez_stats",
        lambda *a, **k: _stats("Suez", "Disrupted", transits=12, synth=False))
    monkeypatch.setattr(
        "data.canal_feed.fetch_panama_stats",
        lambda *a, **k: _stats("Panama", "Normal", synth=True))
    realness = sched.run_canal_sync_job()
    assert realness["suez"]["realness"] == "live"
    assert realness["panama"]["realness"] == "modeled"
    assert CHOKEPOINTS["suez"].current_risk_level == "CRITICAL"
