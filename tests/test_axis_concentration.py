"""Multi-axis (route/chokepoint/commodity) concentration HHI — SPOF (rec R040)."""

from __future__ import annotations

import pytest

from processing.company_concentration_alerts import (
    _hhi_from_shares,
    _normalize_shares,
    _route_to_chokepoints,
    axis_shares,
    chokepoint_shares,
    compute_axis_concentration_alerts,
)


def test_normalize_shares_sums_to_one_and_drops_nonpositive() -> None:
    out = _normalize_shares({"a": 3.0, "b": 1.0, "c": 0.0, "d": -2.0})
    assert out == {"a": pytest.approx(0.75), "b": pytest.approx(0.25)}
    assert _normalize_shares({}) == {}
    assert _normalize_shares({"x": 0.0}) == {}


def test_route_to_chokepoints_inverts_registry() -> None:
    inv = _route_to_chokepoints()
    from processing.chokepoint_analyzer import CHOKEPOINTS
    # Every chokepoint's affected routes must map back to that chokepoint.
    for key, cp in CHOKEPOINTS.items():
        for rid in cp.affected_routes:
            assert key in inv.get(str(rid), [])


def test_chokepoint_shares_is_a_spof_signal() -> None:
    # 90% of the book on a single chokepoint's route -> high HHI; a
    # chokepoint-free book -> empty -> HHI 0 (NOT a chokepoint SPOF).
    r2c = {"r_suez": ["suez"], "r_open": []}
    cp = chokepoint_shares({"r_suez": 0.9, "r_open": 0.1}, route_to_cp=r2c)
    assert cp == {"suez": pytest.approx(0.9)}
    assert _hhi_from_shares(list(cp.values())) == pytest.approx(0.81)

    free = chokepoint_shares({"r_open": 1.0}, route_to_cp=r2c)
    assert free == {} and _hhi_from_shares(list(free.values())) == 0.0


def test_chokepoint_shares_splits_multi_chokepoint_routes() -> None:
    # A route through two chokepoints splits its weight evenly.
    r2c = {"r": ["suez", "babelmandeb"]}
    cp = chokepoint_shares({"r": 1.0}, route_to_cp=r2c)
    assert cp == {"suez": pytest.approx(0.5), "babelmandeb": pytest.approx(0.5)}


def test_axis_shares_real_ticker_structural() -> None:
    # commodity axis sums to ~1; chokepoint axis sums to <= 1 (exposed fraction).
    comm = axis_shares("ZIM", "commodity")
    assert abs(sum(comm.values()) - 1.0) < 1e-6
    cp = axis_shares("ZIM", "chokepoint")
    assert sum(cp.values()) <= 1.0 + 1e-9


def test_alert_fires_critical_on_single_axis_concentration(monkeypatch) -> None:
    import processing.company_concentration_alerts as cc
    # A book wholly dependent on one chokepoint -> HHI 1.0 -> CRITICAL.
    monkeypatch.setattr(cc, "axis_shares",
                        lambda t, axis: {"suez": 1.0} if axis == "chokepoint" else {})
    alerts = cc.compute_axis_concentration_alerts(["ZIM"], axes=("chokepoint",))
    assert len(alerts) == 1
    a = alerts[0]
    assert a.axis == "chokepoint" and a.severity == "CRITICAL"
    assert a.hhi == pytest.approx(1.0)
    assert a.top_keys[0][0] == "suez"


def test_alert_quiet_when_diversified(monkeypatch) -> None:
    import processing.company_concentration_alerts as cc
    # Five equal chokepoints -> HHI 0.2 -> below the 0.45 fire threshold.
    monkeypatch.setattr(cc, "axis_shares",
                        lambda t, axis: {f"cp{i}": 0.2 for i in range(5)})
    assert cc.compute_axis_concentration_alerts(["ZIM"]) == []


def test_compute_never_raises_on_bad_input() -> None:
    assert compute_axis_concentration_alerts([]) == []
    assert compute_axis_concentration_alerts([""]) == []
    # real tickers must not raise (registry-backed)
    out = compute_axis_concentration_alerts(["ZIM", "MATX"])
    assert isinstance(out, list)
    for a in out:
        assert 0.0 <= a.hhi <= 1.0 and a.axis in ("commodity", "route", "chokepoint")
