"""Tests for engine.alert_engine_v2 detection + aggregation + persistence.

The rule-persistence path lives in test_alert_engine_v2_rules.py — this file
covers the detection functions and the alert persistence layer.

Covers:
  - ShippingAlert / AlertRule dataclass shapes
  - _SEVERITY_ORDER mapping
  - _make: returns ShippingAlert with fresh id + isoformat created_at
  - _bdi_series: BDIY / BDI / 'bdi' key fallback; missing → None
  - check_bdi_alerts: short series → empty; 1d move ≥ threshold fires;
    7d move ≥ 10% fires; severity escalation when 2x threshold
  - check_signal_alerts: only HIGH conviction fires; severity by strength
  - check_congestion_alerts: ≤ threshold skipped; tiered severity by score
  - check_rate_alerts: rate-column selection by name; threshold filter;
    severity escalation; missing dataframe gracefully skipped
  - check_stock_alerts: only watchlist tickers (ZIM/MATX/SBLK/DAC/CMRE);
    threshold filter
  - run_all_checks: aggregates + sorts by severity then created_at;
    per-check exceptions swallowed
  - save_alerts + load_alerts + acknowledge_alert/all + get_unread_count
    on an isolated tmp ALERT_FILE
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from engine import alert_engine_v2 as engv2
from engine.alert_engine_v2 import (
    AlertRule,
    ShippingAlert,
    _SEVERITY_ORDER,
    _bdi_series,
    _make,
    acknowledge_alert,
    acknowledge_all,
    check_bdi_alerts,
    check_congestion_alerts,
    check_rate_alerts,
    check_signal_alerts,
    check_stock_alerts,
    get_unread_count,
    load_alerts,
    run_all_checks,
    save_alerts,
)


# ─── Fixture: isolate ALERT_FILE per test ──────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_alert_file(monkeypatch, tmp_path):
    monkeypatch.setattr(engv2, "ALERT_FILE", tmp_path / "alerts.json")


# ─── Stand-ins for duck-typed inputs ────────────────────────────────────────

@dataclass
class _FakeSignal:
    conviction: str = "HIGH"
    ticker: str = "ZIM"
    signal_name: str = "Momentum Breakout"
    direction: str = "LONG"
    strength: float = 0.85
    expected_return_pct: float = 7.5
    time_horizon: str = "2 weeks"
    rationale: str = "Test signal"


@dataclass
class _FakePort:
    locode: str = "USLAX"
    name: str = "Los Angeles"
    congestion_score: float = 0.5


def _bdi_df(values: list[float], key: str = "BDIY") -> dict:
    """Build a macro_data dict with the requested BDI key."""
    return {
        key: pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=len(values), freq="D"),
            "value": values,
        })
    }


def _rate_df(values: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=len(values), freq="D"),
        "rate_usd_per_feu": values,
    })


def _stock_df(values: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=len(values), freq="B"),
        "close": values,
    })


# ─── Dataclasses ────────────────────────────────────────────────────────────

def test_shipping_alert_shape() -> None:
    a = ShippingAlert(
        alert_id="x", created_at="t", alert_type="MACRO", severity="LOW",
        title="t", body="b", ticker="", route_id="", port_locode="",
        value=0.0, threshold=0.0, change_pct=0.0, acknowledged=False,
    )
    assert a.severity == "LOW"
    assert a.acknowledged is False


def test_alert_rule_shape() -> None:
    r = AlertRule(rule_id="r", name="n", alert_type="BDI_MOVE",
                  enabled=True, threshold=5.0, severity="HIGH")
    assert r.enabled is True


# ─── _SEVERITY_ORDER ─────────────────────────────────────────────────────

def test_severity_order_ascending() -> None:
    """CRITICAL < HIGH < MEDIUM < LOW (lower number = more severe = sorted first)."""
    assert _SEVERITY_ORDER["CRITICAL"] < _SEVERITY_ORDER["HIGH"]
    assert _SEVERITY_ORDER["HIGH"] < _SEVERITY_ORDER["MEDIUM"]
    assert _SEVERITY_ORDER["MEDIUM"] < _SEVERITY_ORDER["LOW"]


# ─── _make ──────────────────────────────────────────────────────────────────

def test_make_returns_well_formed_alert() -> None:
    a = _make("BDI_MOVE", "HIGH", "Title", "Body", value=1.2, threshold=5.0)
    assert a.alert_type == "BDI_MOVE"
    assert a.severity == "HIGH"
    assert a.value == 1.2
    assert a.acknowledged is False
    # created_at parses as ISO
    assert datetime.fromisoformat(a.created_at)


def test_make_generates_unique_ids() -> None:
    ids = {_make("MACRO", "LOW", "x", "y").alert_id for _ in range(10)}
    assert len(ids) == 10


# ─── _bdi_series ────────────────────────────────────────────────────────────

def test_bdi_series_finds_bdiy_key() -> None:
    s = _bdi_series(_bdi_df([1000.0, 1100.0], key="BDIY"))
    assert s is not None
    assert list(s) == [1000.0, 1100.0]


def test_bdi_series_falls_back_to_bdi_key() -> None:
    s = _bdi_series(_bdi_df([1000.0, 1100.0], key="BDI"))
    assert s is not None


def test_bdi_series_falls_back_to_lowercase_key() -> None:
    s = _bdi_series(_bdi_df([1000.0, 1100.0], key="bdi"))
    assert s is not None


def test_bdi_series_returns_none_when_no_recognized_key() -> None:
    # 'BSXRLM' is the real FRED key but the alert engine doesn't look for it.
    s = _bdi_series(_bdi_df([1000.0, 1100.0], key="BSXRLM"))
    assert s is None


def test_bdi_series_returns_none_for_empty_df() -> None:
    assert _bdi_series({"BDIY": pd.DataFrame()}) is None


# ─── check_bdi_alerts ───────────────────────────────────────────────────────

def test_check_bdi_alerts_empty_returns_empty() -> None:
    assert check_bdi_alerts({}) == []


def test_check_bdi_alerts_short_series_returns_empty() -> None:
    assert check_bdi_alerts(_bdi_df([1000.0])) == []


def test_check_bdi_alerts_under_threshold_no_fire() -> None:
    """+3% move with default 5% threshold → no alert."""
    out = check_bdi_alerts(_bdi_df([1000.0, 1030.0]))
    assert out == []


def test_check_bdi_alerts_1d_move_fires_high_severity() -> None:
    """+6% move ≥ 5% threshold but < 10% (2× threshold) → HIGH."""
    out = check_bdi_alerts(_bdi_df([1000.0, 1060.0]))
    assert len(out) == 1
    assert out[0].alert_type == "BDI_MOVE"
    assert out[0].severity == "HIGH"


def test_check_bdi_alerts_1d_move_escalates_to_critical_at_2x() -> None:
    """+12% move ≥ 2× threshold (10%) → CRITICAL."""
    out = check_bdi_alerts(_bdi_df([1000.0, 1120.0]))
    assert any(a.severity == "CRITICAL" for a in out)


def test_check_bdi_alerts_7d_move_fires_when_over_10pct() -> None:
    """8 obs needed; +12% over 7d → 7d alert fires too."""
    vals = [1000.0] * 7 + [1120.0]
    out = check_bdi_alerts(_bdi_df(vals))
    titles = [a.title for a in out]
    assert any("7 Days" in t for t in titles)


# ─── check_signal_alerts ───────────────────────────────────────────────────

def test_check_signal_alerts_empty_returns_empty() -> None:
    assert check_signal_alerts([]) == []
    assert check_signal_alerts(None) == []


def test_check_signal_alerts_only_high_conviction_fires() -> None:
    sigs = [_FakeSignal(conviction="LOW"), _FakeSignal(conviction="HIGH"),
            _FakeSignal(conviction="MEDIUM")]
    out = check_signal_alerts(sigs)
    assert len(out) == 1
    assert out[0].alert_type == "SIGNAL_FIRE"


def test_check_signal_alerts_severity_by_strength() -> None:
    out = check_signal_alerts([_FakeSignal(strength=0.85)])
    assert out[0].severity == "HIGH"
    out = check_signal_alerts([_FakeSignal(strength=0.50)])
    assert out[0].severity == "MEDIUM"


def test_check_signal_alerts_truncates_long_rationale() -> None:
    long_rationale = "A" * 500
    out = check_signal_alerts([_FakeSignal(rationale=long_rationale)])
    assert "AAAAA..." in out[0].body
    assert len(out[0].body) < 600


# ─── check_congestion_alerts ───────────────────────────────────────────────

def test_check_congestion_alerts_empty_returns_empty() -> None:
    assert check_congestion_alerts([]) == []


def test_check_congestion_alerts_under_threshold_no_fire() -> None:
    ports = [_FakePort(congestion_score=0.60)]
    assert check_congestion_alerts(ports, threshold=0.75) == []


def test_check_congestion_alerts_tiered_severity() -> None:
    """0.95 → CRITICAL, 0.85 → HIGH, 0.78 → MEDIUM."""
    crit = check_congestion_alerts([_FakePort(congestion_score=0.95)], threshold=0.75)
    high = check_congestion_alerts([_FakePort(congestion_score=0.85)], threshold=0.75)
    med = check_congestion_alerts([_FakePort(congestion_score=0.78)], threshold=0.75)
    assert crit[0].severity == "CRITICAL"
    assert high[0].severity == "HIGH"
    assert med[0].severity == "MEDIUM"


def test_check_congestion_alerts_skips_ports_without_score() -> None:
    @dataclass
    class _NoScore:
        locode: str = "X"
        name: str = "x"
    assert check_congestion_alerts([_NoScore()]) == []


# ─── check_rate_alerts ─────────────────────────────────────────────────────

def test_check_rate_alerts_empty_returns_empty() -> None:
    assert check_rate_alerts({}) == []


def test_check_rate_alerts_under_threshold_no_fire() -> None:
    """+5% over 7d with default 8% threshold → no alert."""
    out = check_rate_alerts({"r": _rate_df([1000.0] * 7 + [1050.0])})
    assert out == []


def test_check_rate_alerts_fires_on_significant_move() -> None:
    """+15% over 7d ≥ 8% threshold → HIGH (under 2× threshold)."""
    out = check_rate_alerts({"r": _rate_df([1000.0] * 7 + [1150.0])})
    assert len(out) == 1
    assert out[0].alert_type == "RATE_SURGE"
    assert out[0].severity == "HIGH"


def test_check_rate_alerts_critical_at_2x_threshold() -> None:
    """+20% over 7d ≥ 16% (2× 8%) → CRITICAL."""
    out = check_rate_alerts({"r": _rate_df([1000.0] * 7 + [1200.0])})
    assert out[0].severity == "CRITICAL"


def test_check_rate_alerts_skips_frames_without_rate_column() -> None:
    df = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=10),
                       "other": [1] * 10})
    assert check_rate_alerts({"r": df}) == []


def test_check_rate_alerts_skips_empty_dfs() -> None:
    assert check_rate_alerts({"r": pd.DataFrame()}) == []


# ─── check_stock_alerts ─────────────────────────────────────────────────────

def test_check_stock_alerts_only_watchlist_tickers() -> None:
    """Non-watchlist tickers ignored, even with big moves."""
    out = check_stock_alerts({"AAPL": _stock_df([100.0, 130.0])})
    assert out == []


def test_check_stock_alerts_fires_on_watchlist_move() -> None:
    """ZIM +15% in 1 day ≥ 8% threshold → fires."""
    out = check_stock_alerts({"ZIM": _stock_df([10.0, 11.5])})
    assert len(out) == 1
    assert out[0].alert_type == "STOCK_MOVE"
    assert out[0].ticker == "ZIM"


def test_check_stock_alerts_critical_at_1_75x_threshold() -> None:
    """ZIM +15% with threshold=8% → 15 ≥ 14 (8 × 1.75) → CRITICAL."""
    out = check_stock_alerts({"ZIM": _stock_df([10.0, 11.5])})
    assert out[0].severity == "CRITICAL"


def test_check_stock_alerts_under_threshold_no_fire() -> None:
    out = check_stock_alerts({"ZIM": _stock_df([10.0, 10.5])})  # +5%
    assert out == []


# ─── run_all_checks ────────────────────────────────────────────────────────

def test_run_all_checks_aggregates_and_sorts_by_severity() -> None:
    macro = _bdi_df([1000.0, 1120.0])  # +12% → CRITICAL
    stocks = {"ZIM": _stock_df([10.0, 10.85])}  # +8.5% → HIGH
    out = run_all_checks([], [], [], {}, macro, stocks)
    assert len(out) >= 2
    severities = [_SEVERITY_ORDER[a.severity] for a in out]
    assert severities == sorted(severities)


def test_run_all_checks_swallows_per_check_errors() -> None:
    """Garbage input shouldn't bring down aggregation — each check runs in
    its own try/except."""
    out = run_all_checks([], [], [], {"r": "not a df"}, {}, {})
    assert isinstance(out, list)


def test_run_all_checks_empty_inputs_returns_empty() -> None:
    out = run_all_checks([], [], [], {}, {}, {})
    assert out == []


# ─── Alert persistence ────────────────────────────────────────────────────

def _mk_alert(severity: str = "HIGH", created_at: str | None = None,
              ack: bool = False) -> ShippingAlert:
    a = _make("MACRO", severity, "t", "b")
    if created_at is not None:
        a.created_at = created_at
    a.acknowledged = ack
    return a


def test_save_and_load_alerts_round_trip() -> None:
    a = _mk_alert()
    save_alerts([a])
    loaded = load_alerts()
    assert len(loaded) == 1
    assert loaded[0].alert_id == a.alert_id


def test_save_alerts_dedups_by_id() -> None:
    a = _mk_alert()
    save_alerts([a])
    save_alerts([a])  # same id again
    loaded = load_alerts()
    assert len(loaded) == 1


def test_load_alerts_filters_by_max_age_days() -> None:
    """Alerts older than max_age_days are excluded."""
    old = _mk_alert(created_at=(datetime.now(timezone.utc) - timedelta(days=60)).isoformat())
    fresh = _mk_alert(created_at=datetime.now(timezone.utc).isoformat())
    save_alerts([old, fresh])
    loaded = load_alerts(max_age_days=30)
    ids = {a.alert_id for a in loaded}
    assert fresh.alert_id in ids
    assert old.alert_id not in ids


def test_acknowledge_alert_persists() -> None:
    a = _mk_alert()
    save_alerts([a])
    acknowledge_alert(a.alert_id)
    loaded = load_alerts()
    assert loaded[0].acknowledged is True


def test_acknowledge_all_marks_every_record() -> None:
    save_alerts([_mk_alert(), _mk_alert(), _mk_alert()])
    acknowledge_all()
    loaded = load_alerts()
    assert all(a.acknowledged for a in loaded)


def test_get_unread_count_returns_unacknowledged_total() -> None:
    save_alerts([_mk_alert(ack=False), _mk_alert(ack=False),
                _mk_alert(ack=True)])
    assert get_unread_count() == 2


def test_load_alerts_returns_empty_when_file_missing() -> None:
    """Fresh fixture → ALERT_FILE does not exist yet."""
    assert load_alerts() == []


def test_save_alerts_caps_at_max_stored(monkeypatch) -> None:
    """Stored records trim to _MAX_STORED, keeping the newest."""
    monkeypatch.setattr(engv2, "_MAX_STORED", 3)
    base = datetime.now(timezone.utc)
    alerts = [
        _mk_alert(created_at=(base + timedelta(seconds=i)).isoformat())
        for i in range(5)
    ]
    save_alerts(alerts)
    loaded = load_alerts(max_age_days=30)
    assert len(loaded) == 3
