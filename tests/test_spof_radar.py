"""Defining-property tests for processing/spof_radar.py (rec R030).

A multi-dimension single-point-of-failure radar: a book concentrated on ONE
chokepoint scores high on the chokepoint axis but low on a diversified axis; a
chokepoint-free / diversified book scores ~0 (no SPOF); each axis is computed
independently; the dominant node is identified; ``spof_alerts`` fires one alert
per breached axis and none when diversified; an empty book → zeros, no crash;
nothing raises.
"""
from __future__ import annotations

import pytest

from processing.spof_radar import (
    SPOF_AXES,
    SPOF_CRITICAL_THRESHOLD,
    SPOF_FIRE_THRESHOLD,
    AxisSpof,
    SpofRadar,
    compute_spof_radar,
    spof_alerts,
)


def _axis(radar: SpofRadar, name: str) -> AxisSpof:
    return next(a for a in radar.axes if a.axis == name)


# ── HHI helper reuse + threshold sanity ──────────────────────────────────


def test_hhi_helper_is_reused_not_reimplemented() -> None:
    """R030 must reuse company_supply_risk._hhi, not roll its own."""
    import processing.spof_radar as sr
    from processing.company_supply_risk import _hhi as canonical_hhi

    assert sr._hhi is canonical_hhi


def test_thresholds_mirror_port_concentration_ladder() -> None:
    assert SPOF_FIRE_THRESHOLD == pytest.approx(0.45)
    assert SPOF_CRITICAL_THRESHOLD == pytest.approx(0.85)
    assert set(SPOF_AXES) == {"chokepoint", "origin", "carrier", "commodity"}


# ── Empty / degenerate book → zeros, no crash ────────────────────────────


def test_empty_book_scores_all_zeros_no_fire() -> None:
    radar = compute_spof_radar({})
    assert isinstance(radar, SpofRadar)
    assert len(radar.axes) == len(SPOF_AXES)
    assert all(a.score == 0.0 for a in radar.axes)
    assert all(not a.fired and not a.critical for a in radar.axes)
    assert not radar.is_spof
    assert radar.worst_axis == ""
    assert radar.n_breached == 0
    assert spof_alerts(radar) == []


def test_all_zero_weight_book_is_treated_as_empty() -> None:
    radar = compute_spof_radar({"ZIM": 0.0, "SBLK": 0.0})
    assert not radar.is_spof
    assert all(a.score == 0.0 for a in radar.axes)


# ── Carrier axis: single-name SPOF ───────────────────────────────────────


def test_carrier_axis_90pct_one_name_scores_081() -> None:
    """A 90%-one-carrier book → HHI 0.9²+0.1² = 0.82 on the carrier axis,
    and the dominant node is that carrier."""
    radar = compute_spof_radar(
        {"ZIM": 0.9, "SBLK": 0.1}, exposure={}, chokepoints={}
    )
    carrier = _axis(radar, "carrier")
    assert carrier.score == pytest.approx(0.82, abs=1e-6)
    assert carrier.dominant_node == "ZIM"
    assert carrier.dominant_share == pytest.approx(0.9)
    assert carrier.fired and not carrier.critical
    assert carrier.metric == "HHI"


def test_carrier_axis_diversified_does_not_fire() -> None:
    """Five equal names → HHI 0.2, below the 0.45 fire threshold."""
    radar = compute_spof_radar(
        {t: 1.0 for t in ("A", "B", "C", "D", "E")}, exposure={}, chokepoints={}
    )
    carrier = _axis(radar, "carrier")
    assert carrier.score == pytest.approx(0.2)
    assert not carrier.fired


# ── Commodity axis: independent of the carrier axis ──────────────────────


def test_commodity_axis_independent_of_carrier_axis() -> None:
    """Two equally-weighted names that BOTH carry only 'electronics' → carrier
    axis diversified (0.5) but commodity axis is single-bucket (1.0). Proves the
    axes are computed independently."""
    exposure = {"X": {"electronics": 1.0}, "Y": {"electronics": 1.0}}
    radar = compute_spof_radar({"X": 0.5, "Y": 0.5}, exposure=exposure, chokepoints={})
    carrier = _axis(radar, "carrier")
    commodity = _axis(radar, "commodity")
    assert carrier.score == pytest.approx(0.5)         # two equal names
    assert commodity.score == pytest.approx(1.0)        # one commodity
    assert commodity.dominant_node == "electronics"
    assert commodity.critical


# ── Chokepoint axis: serial SPOF, max single-key exposure ────────────────


def test_chokepoint_axis_uses_serial_max_metric_when_all_lanes_share_a_choke() -> None:
    """When EVERY lane the book touches transits one chokepoint, that chokepoint
    carries the book's full route weight → max single-key exposure = 1.0
    (serial SPOF, NOT a 1/K split). Built by routing a single-commodity book onto
    its real lanes and pinning every one of those lanes to one chokepoint."""
    from processing.exposure_matrix import routes_for_commodity

    cats = ["electronics", "metals", "agriculture", "machinery", "chemicals"]
    c, routes = next(
        (c, routes_for_commodity(c)) for c in cats if routes_for_commodity(c)
    )
    # Every lane this single-commodity book touches passes through 'choke_a'.
    r2c = {rid: ["choke_a"] for rid in routes}
    radar = compute_spof_radar({"A": 1.0}, exposure={"A": {c: 1.0}}, chokepoints=r2c)
    cp = _axis(radar, "chokepoint")
    assert cp.metric == "max single-key exposure"
    assert cp.dominant_node == "choke_a"
    # Route shares (a partition) sum to 1 and ALL pass choke_a → exposure 1.0.
    assert cp.score == pytest.approx(1.0, abs=1e-6)
    assert cp.fired and cp.critical


def test_chokepoint_free_book_scores_zero_on_chokepoint_axis() -> None:
    """A book whose lanes pass no chokepoint → chokepoint score 0 (no SPOF)."""
    from processing.exposure_matrix import routes_for_commodity

    cats = ["electronics", "metals", "agriculture", "machinery", "chemicals"]
    c, routes = next(
        (c, routes_for_commodity(c)) for c in cats if routes_for_commodity(c)
    )
    r2c = {rid: [] for rid in routes}  # every lane chokepoint-free
    radar = compute_spof_radar({"A": 1.0}, exposure={"A": {c: 1.0}}, chokepoints=r2c)
    cp = _axis(radar, "chokepoint")
    assert cp.score == 0.0
    assert not cp.fired
    assert cp.dominant_node == ""


# ── Origin axis ──────────────────────────────────────────────────────────


def test_origin_axis_rolls_up_to_origin_region() -> None:
    """Real ZIM-heavy book: the origin axis identifies a real registry region
    as the dominant cargo origin and stays in [0, 1]."""
    radar = compute_spof_radar({"ZIM": 0.9, "SBLK": 0.1})
    origin = _axis(radar, "origin")
    assert 0.0 <= origin.score <= 1.0
    assert origin.metric == "HHI"
    if origin.n_buckets:
        assert origin.dominant_node  # a real region string, e.g. "Asia East"


# ── Real registries: structural invariants ───────────────────────────────


def test_real_book_partition_axes_sum_to_one_chokepoint_in_range() -> None:
    """commodity/origin are partitions (HHI in [1/n, 1]); chokepoint is serial
    (max exposure in [0, 1]). All scores stay in [0, 1] for a real book."""
    radar = compute_spof_radar({"ZIM": 0.5, "SBLK": 0.3, "MATX": 0.2})
    for a in radar.axes:
        assert 0.0 <= a.score <= 1.0, (a.axis, a.score)
        assert a.band
    assert radar.worst_score == max(a.score for a in radar.axes)


def test_dominant_node_is_the_highest_share_key() -> None:
    radar = compute_spof_radar(
        {"ZIM": 0.7, "SBLK": 0.2, "MATX": 0.1}, exposure={}, chokepoints={}
    )
    carrier = _axis(radar, "carrier")
    assert carrier.dominant_node == "ZIM"
    assert carrier.dominant_share == pytest.approx(0.7)
    # top_keys are sorted by share desc
    shares = [s for _k, s in carrier.top_keys]
    assert shares == sorted(shares, reverse=True)


# ── spof_alerts: one alert per breached axis ─────────────────────────────


def test_spof_alerts_fires_one_per_breached_axis() -> None:
    """A book concentrated on BOTH carrier and commodity → two alerts (one per
    breached axis); a diversified axis contributes none."""
    exposure = {"X": {"electronics": 1.0}, "Y": {"electronics": 1.0}}
    radar = compute_spof_radar(
        {"X": 0.95, "Y": 0.05}, exposure=exposure, chokepoints={}
    )
    alerts = spof_alerts(radar)
    fired_axes = {a.route_id for a in alerts}
    # carrier (0.95²+0.05² ≈ 0.905) and commodity (single bucket = 1.0) both fire.
    assert "carrier" in fired_axes
    assert "commodity" in fired_axes
    assert all(al.alert_type == "SPOF_DIMENSION" for al in alerts)
    # one alert per breached axis
    breached = {a.axis for a in radar.axes if a.fired}
    assert fired_axes == breached
    assert len(alerts) == len(breached)


def test_spof_alerts_severity_tracks_critical_flag() -> None:
    radar = compute_spof_radar({"ZIM": 0.95, "SBLK": 0.05}, exposure={}, chokepoints={})
    alerts = spof_alerts(radar)
    carrier_alert = next(a for a in alerts if a.route_id == "carrier")
    # 0.95²+0.05² = 0.905 >= 0.85 critical threshold
    assert carrier_alert.severity == "CRITICAL"
    assert "ZIM" in carrier_alert.body


def test_spof_alerts_none_when_fully_diversified() -> None:
    """An equal-weight book across many names with a spread commodity mix fires
    nothing on any axis."""
    book = {t: 1.0 for t in ("A", "B", "C", "D", "E", "F", "G", "H")}
    exposure = {
        t: {"electronics": 0.25, "metals": 0.25, "agriculture": 0.25, "machinery": 0.25}
        for t in book
    }
    radar = compute_spof_radar(book, exposure=exposure, chokepoints={})
    assert not radar.is_spof
    assert spof_alerts(radar) == []


# ── Never raises ─────────────────────────────────────────────────────────


def test_never_raises_on_garbage_input() -> None:
    # Unknown tickers, negative weights, None-ish — all must degrade, not raise.
    radar = compute_spof_radar(
        {"NOPE": 0.5, "ALSO_NOPE": 0.5, "NEG": -1.0}, exposure={}, chokepoints={}
    )
    assert isinstance(radar, SpofRadar)
    assert isinstance(spof_alerts(radar), list)
    # None radar guarded
    assert spof_alerts(None) == []


def test_real_default_registries_do_not_raise() -> None:
    """The real exposure map + chokepoint registry path must not raise."""
    radar = compute_spof_radar({"ZIM": 0.4, "SBLK": 0.3, "GOGL": 0.3})
    assert isinstance(radar, SpofRadar)
    for a in radar.axes:
        assert 0.0 <= a.score <= 1.0
