"""Defining-property tests for processing/cargo_flow_anomaly.py."""
from __future__ import annotations

import math

import pytest

from processing.cargo_flow_anomaly import (
    CARGO_DRIFT_BANDS,
    CategoryJump,
    compute_cargo_flow_anomaly,
    jensen_shannon_divergence,
)


# ── Jensen-Shannon divergence ────────────────────────────────────────────


def test_jsd_of_identical_distributions_is_zero() -> None:
    p = {"electronics": 0.5, "apparel": 0.3, "machinery": 0.2}
    assert jensen_shannon_divergence(p, p) == 0.0


def test_jsd_of_disjoint_distributions_is_one() -> None:
    """JSD with log base 2 maxes at 1.0 for distributions with disjoint supports."""
    p = {"A": 1.0}
    q = {"B": 1.0}
    assert jensen_shannon_divergence(p, q) == pytest.approx(1.0)


def test_jsd_symmetric_in_arguments() -> None:
    p = {"A": 0.7, "B": 0.3}
    q = {"A": 0.3, "B": 0.7}
    assert jensen_shannon_divergence(p, q) == pytest.approx(
        jensen_shannon_divergence(q, p),
    )


def test_jsd_handles_unnormalized_inputs() -> None:
    """Raw share dicts that sum to != 1.0 still produce a sensible result."""
    p_raw = {"A": 50, "B": 50}            # totals 100
    p_norm = {"A": 0.5, "B": 0.5}
    q = {"A": 0.5, "B": 0.5}
    # Both should give 0 since after normalization they are identical
    assert jensen_shannon_divergence(p_raw, q) == 0.0
    assert jensen_shannon_divergence(p_norm, q) == 0.0


def test_jsd_empty_distributions_return_zero() -> None:
    assert jensen_shannon_divergence({}, {"A": 1.0}) == 0.0
    assert jensen_shannon_divergence({"A": 1.0}, {}) == 0.0
    assert jensen_shannon_divergence({}, {}) == 0.0


# ── Anomaly detection — stable cargo flow ────────────────────────────────


def test_stable_history_reports_no_anomaly() -> None:
    """Same mix every day → JSD=0, no jumps, is_anomaly=False."""
    mix = {"electronics": 0.4, "apparel": 0.3, "machinery": 0.3}
    history = [mix] * 14
    r = compute_cargo_flow_anomaly(
        route_id="transpacific_eb",
        today_mix=mix,
        history=history,
    )
    assert r.jsd == 0.0
    assert r.drift_band == "stable"
    assert r.surges == []
    assert r.collapses == []
    assert r.is_anomaly is False


def test_small_drift_below_threshold_not_flagged() -> None:
    """A 5pp shift on one category with default threshold (10pp) → no flag."""
    base = {"electronics": 0.40, "apparel": 0.30, "machinery": 0.30}
    today = {"electronics": 0.45, "apparel": 0.25, "machinery": 0.30}
    history = [base] * 14
    r = compute_cargo_flow_anomaly(
        route_id="x",
        today_mix=today,
        history=history,
    )
    assert r.surges == []
    assert r.collapses == []


# ── Surge / collapse detection ──────────────────────────────────────────


def test_surge_detected_when_category_jumps_above_threshold() -> None:
    base = {"electronics": 0.40, "apparel": 0.30, "machinery": 0.30}
    today = {"electronics": 0.20, "apparel": 0.30, "agriculture": 0.50}
    history = [base] * 14
    r = compute_cargo_flow_anomaly(
        route_id="x",
        today_mix=today,
        history=history,
        jump_threshold_pp=10.0,
    )
    surge_cats = {j.category for j in r.surges}
    assert "agriculture" in surge_cats
    # Agriculture went from 0% baseline → 50% today → +50pp
    ag = next(j for j in r.surges if j.category == "agriculture")
    assert ag.delta_pp == pytest.approx(50.0)
    assert ag.direction == "surge"


def test_collapse_detected_when_category_drops_below_threshold() -> None:
    base = {"electronics": 0.40, "apparel": 0.30, "machinery": 0.30}
    today = {"electronics": 0.05, "apparel": 0.30, "machinery": 0.65}
    history = [base] * 14
    r = compute_cargo_flow_anomaly(
        route_id="x",
        today_mix=today,
        history=history,
        jump_threshold_pp=10.0,
    )
    collapse_cats = {j.category for j in r.collapses}
    assert "electronics" in collapse_cats
    el = next(j for j in r.collapses if j.category == "electronics")
    assert el.delta_pp == pytest.approx(-35.0)
    assert el.direction == "collapse"


def test_surges_sorted_descending_by_delta() -> None:
    base = {"a": 0.5, "b": 0.5}
    today = {"a": 0.1, "x": 0.4, "y": 0.5}
    history = [base] * 14
    r = compute_cargo_flow_anomaly(
        route_id="x", today_mix=today, history=history,
        jump_threshold_pp=10.0,
    )
    delta_seq = [j.delta_pp for j in r.surges]
    assert delta_seq == sorted(delta_seq, reverse=True)


def test_collapses_sorted_ascending_by_delta() -> None:
    """Most-negative (biggest collapse) first."""
    base = {"a": 0.4, "b": 0.3, "c": 0.3}
    today = {"a": 0.05, "b": 0.05, "c": 0.05, "x": 0.85}
    history = [base] * 14
    r = compute_cargo_flow_anomaly(
        route_id="x", today_mix=today, history=history,
        jump_threshold_pp=10.0,
    )
    delta_seq = [j.delta_pp for j in r.collapses]
    assert delta_seq == sorted(delta_seq)


# ── JSD-driven anomaly band ─────────────────────────────────────────────


def test_full_mix_swap_lands_in_shock_band() -> None:
    """today is completely disjoint from the baseline → JSD=1.0 → shock band."""
    base = {"electronics": 0.5, "apparel": 0.5}
    today = {"agriculture": 0.5, "chemicals": 0.5}
    history = [base] * 14
    r = compute_cargo_flow_anomaly(
        route_id="x", today_mix=today, history=history,
    )
    assert r.jsd >= 0.30
    assert r.drift_band == "shock"
    assert r.is_anomaly is True


def test_moderate_drift_lands_in_elevated_or_anomalous() -> None:
    """A partial shift bumps JSD into elevated/anomalous (NOT shock, NOT stable)."""
    base = {"electronics": 0.5, "apparel": 0.5}
    today = {"electronics": 0.3, "apparel": 0.4, "chemicals": 0.3}
    history = [base] * 14
    r = compute_cargo_flow_anomaly(
        route_id="x", today_mix=today, history=history,
    )
    assert r.drift_band in ("elevated", "anomalous")


# ── Defensive handling ─────────────────────────────────────────────────


def test_empty_history_returns_no_anomaly() -> None:
    today = {"electronics": 1.0}
    r = compute_cargo_flow_anomaly(
        route_id="x", today_mix=today, history=[],
    )
    assert r.is_anomaly is False
    assert r.jsd == 0.0
    assert "insufficient data" in r.summary


def test_empty_today_mix_returns_no_anomaly() -> None:
    history = [{"a": 1.0}] * 14
    r = compute_cargo_flow_anomaly(
        route_id="x", today_mix={}, history=history,
    )
    assert r.is_anomaly is False


def test_trailing_window_clamps_to_one_when_negative() -> None:
    base = {"a": 1.0}
    r = compute_cargo_flow_anomaly(
        route_id="x", today_mix=base, history=[base] * 5,
        trailing_window=-1,
    )
    # Just confirm we got a defensible report and no exception.
    assert r.jsd == 0.0


def test_summary_string_includes_route_id_and_jsd() -> None:
    base = {"a": 0.5, "b": 0.5}
    today = {"a": 0.1, "b": 0.1, "c": 0.8}
    r = compute_cargo_flow_anomaly(
        route_id="transpacific_eb", today_mix=today, history=[base] * 14,
    )
    assert "transpacific_eb" in r.summary
    assert "JSD=" in r.summary
