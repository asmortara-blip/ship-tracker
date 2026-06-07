"""Tests for the live GDELT→chokepoint risk overlay (rec R014).

The overlay reassigns module-level CHOKEPOINTS entries, so every test that
exercises a REAL escalation snapshots + restores the touched entries via
``restore_chokepoints`` — the overlay must never leak across tests (mirrors
``test_canal_chokepoint_sync``).

OFFLINE: no network. GDELT events are passed in as fixtures.
"""
from __future__ import annotations

import dataclasses

import pytest

from data.gdelt_feed import GdeltEvent
from processing.chokepoint_analyzer import CHOKEPOINTS, apply_live_chokepoint_risk


_CANAL_KEYS = ("suez", "panama", "hormuz", "bab_el_mandeb", "malacca",
               "gibraltar", "danish_straits", "dover", "lombok_sunda")


@pytest.fixture
def restore_chokepoints():
    """Snapshot + restore the FULL registry so the overlay never leaks."""
    snap = {k: dataclasses.replace(CHOKEPOINTS[k]) for k in _CANAL_KEYS}
    yield
    for k, v in snap.items():
        CHOKEPOINTS[k] = v


def _volume_for(cp_key: str, n: int) -> list:
    """``n`` disruption-news articles attributed to a chokepoint's country.

    The new signal is VOLUME (article COUNT), not tone — each event is stamped
    with the chokepoint's coords AND key so the signal attributes them back. With
    the documented cutoffs: n>=120 → CRITICAL, n>=50 → HIGH, n>=15 → MODERATE,
    n>=1 → LOW, n==0 → dark.
    """
    cp = CHOKEPOINTS[cp_key]
    return [GdeltEvent(lat=cp.lat, lon=cp.lon, title="t", url="u",
                       seen_at="", chokepoint_key=cp_key) for _ in range(n)]


# ── default / no-data == EXACT current behavior ──────────────────────────────

def test_overlay_default_is_no_op() -> None:
    """No live data → empty realness map, registry untouched (the baseline
    determinism the whole suite relies on)."""
    before = {k: CHOKEPOINTS[k].current_risk_level for k in _CANAL_KEYS}
    marker = apply_live_chokepoint_risk()
    assert marker == {}
    assert {k: CHOKEPOINTS[k].current_risk_level for k in _CANAL_KEYS} == before


def test_overlay_empty_events_is_no_op(restore_chokepoints) -> None:
    before = {k: CHOKEPOINTS[k].current_risk_level for k in _CANAL_KEYS}
    assert apply_live_chokepoint_risk(gdelt_events=[]) == {}
    assert {k: CHOKEPOINTS[k].current_risk_level for k in _CANAL_KEYS} == before


def test_overlay_none_events_is_no_op(restore_chokepoints) -> None:
    assert apply_live_chokepoint_risk(gdelt_events=None) == {}


# ── real GDELT cluster ESCALATES ─────────────────────────────────────────────

def test_overlay_escalates_a_calm_node_on_real_cluster(restore_chokepoints) -> None:
    """A high disruption-news volume for Malacca's country (baseline LOW) escalates it."""
    assert CHOKEPOINTS["malacca"].current_risk_level == "LOW"  # baseline
    events = _volume_for("malacca", 120)
    marker = apply_live_chokepoint_risk(gdelt_events=events)
    assert marker["malacca"]["realness"] == "live"
    assert marker["malacca"]["source"] == "gdelt"
    assert "as_of" in marker["malacca"]
    assert CHOKEPOINTS["malacca"].current_risk_level == "CRITICAL"
    assert CHOKEPOINTS["malacca"].current_disruption_type == "ACTIVE_CONFLICT"


def test_overlay_escalates_suez_on_real_cluster(restore_chokepoints) -> None:
    # Suez baseline is already CRITICAL; a high news-volume read keeps CRITICAL
    # and still reports a live realness marker (real signal corroborating).
    events = _volume_for("suez", 120)
    marker = apply_live_chokepoint_risk(gdelt_events=events)
    assert marker.get("suez", {}).get("realness") == "live"
    assert CHOKEPOINTS["suez"].current_risk_level == "CRITICAL"


# ── never downgrades ─────────────────────────────────────────────────────────

def test_overlay_never_downgrades_baseline(restore_chokepoints) -> None:
    """A real-but-mild GDELT read near Hormuz (baseline HIGH) must not lower it."""
    assert CHOKEPOINTS["hormuz"].current_risk_level == "HIGH"
    benign = _volume_for("hormuz", 3)  # few articles → derived LOW
    apply_live_chokepoint_risk(gdelt_events=benign)
    assert CHOKEPOINTS["hormuz"].current_risk_level == "HIGH"  # unchanged
    assert CHOKEPOINTS["hormuz"].current_disruption_type == "DIPLOMATIC"  # cause kept


# ── far events never escalate ────────────────────────────────────────────────

def test_overlay_far_events_are_no_op(restore_chokepoints) -> None:
    before = CHOKEPOINTS["dover"].current_risk_level
    far = [GdeltEvent(lat=-40.0, lon=-120.0, title="t", url="u", seen_at="",
                      tone=-9.0) for _ in range(12)]
    marker = apply_live_chokepoint_risk(gdelt_events=far)
    assert "dover" not in marker
    assert CHOKEPOINTS["dover"].current_risk_level == before


# ── global registry is reassigned, not mutated in place ──────────────────────

def test_overlay_does_not_mutate_in_place(restore_chokepoints) -> None:
    """The escalation replaces the entry with a NEW object (R007 safety)."""
    original_obj = CHOKEPOINTS["malacca"]
    events = _volume_for("malacca", 120)
    apply_live_chokepoint_risk(gdelt_events=events)
    # New object identity; the snapshotted original is unmodified.
    assert CHOKEPOINTS["malacca"] is not original_obj
    assert original_obj.current_risk_level == "LOW"


def test_overlay_idempotent(restore_chokepoints) -> None:
    events = _volume_for("malacca", 120)
    apply_live_chokepoint_risk(gdelt_events=events)
    first = dataclasses.replace(CHOKEPOINTS["malacca"])
    apply_live_chokepoint_risk(gdelt_events=events)
    after = CHOKEPOINTS["malacca"]
    assert after.current_risk_level == first.current_risk_level
    assert after.current_disruption_type == first.current_disruption_type


# ── canal delegation (real-only) still works through the combined overlay ─────

def test_overlay_delegates_real_canal_state(restore_chokepoints) -> None:
    from data.canal_feed import CanalStats
    real_suez = CanalStats(
        canal="Suez", northbound_wait_days=1.0, southbound_wait_days=1.0,
        daily_transits=12, capacity_utilization_pct=40.0, water_level_m=0.0,
        status="Disrupted", restrictions="", source_url="", fetched_at="",
        is_synthetic=False,
    )
    marker = apply_live_chokepoint_risk(canal_stats=[real_suez])
    assert marker.get("suez", {}).get("realness") == "live"
    assert CHOKEPOINTS["suez"].current_risk_level == "CRITICAL"


def test_overlay_skips_synthetic_canal(restore_chokepoints) -> None:
    from data.canal_feed import CanalStats
    synth_panama = CanalStats(
        canal="Panama", northbound_wait_days=1.0, southbound_wait_days=1.0,
        daily_transits=34, capacity_utilization_pct=78.0, water_level_m=27.0,
        status="Disrupted", restrictions="", source_url="", fetched_at="",
        is_synthetic=True,
    )
    before = CHOKEPOINTS["panama"].current_risk_level
    marker = apply_live_chokepoint_risk(canal_stats=[synth_panama])
    assert marker.get("panama", {}).get("realness") == "modeled"
    assert CHOKEPOINTS["panama"].current_risk_level == before  # untouched


# ── R014 scheduler wiring: run_gdelt_chokepoint_job ──────────────────────────
def test_gdelt_job_offline_is_ok_and_keeps_baseline(monkeypatch, restore_chokepoints) -> None:
    """An unavailable feed → the job still 'ran' (ok=True — a data condition, not
    a job failure, so we don't hammer GDELT's rate limit) and the registry keeps
    its honest modeled baseline."""
    import data.gdelt_feed as gf
    from worker.scheduler import run_gdelt_chokepoint_job

    before = {k: CHOKEPOINTS[k].current_risk_level for k in _CANAL_KEYS}
    monkeypatch.setattr(gf, "fetch_chokepoint_events",
                        lambda *a, **k: ([], "unavailable"))
    out = run_gdelt_chokepoint_job()
    assert out["ok"] is True
    assert out["realness"] == "unavailable"
    assert out["live"] == []
    assert {k: CHOKEPOINTS[k].current_risk_level for k in _CANAL_KEYS} == before


def test_gdelt_job_escalates_registry_on_live_cluster(monkeypatch, restore_chokepoints) -> None:
    """A real high news-volume read for Malacca (baseline LOW) → the job escalates the
    registry node that the SSI chokepoint component then reads (the whole point of
    R014: the headline stress index can finally move on a real escalation)."""
    import data.gdelt_feed as gf
    from worker.scheduler import run_gdelt_chokepoint_job

    assert CHOKEPOINTS["malacca"].current_risk_level == "LOW"  # baseline
    events = _volume_for("malacca", 120)
    monkeypatch.setattr(gf, "fetch_chokepoint_events",
                        lambda *a, **k: (events, "live"))
    out = run_gdelt_chokepoint_job()
    assert out["ok"] is True
    assert "malacca" in out["live"]
    assert CHOKEPOINTS["malacca"].current_risk_level == "CRITICAL"
