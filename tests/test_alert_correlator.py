"""Tests for engine.alert_correlator — grouping related alerts into incidents.

Covers:
  - AlertIncident dataclass shape (defaults, types, entities_touched
    has stable keys)
  - correlate_alerts on empty input → empty list
  - correlate_alerts on a single alert → one incident with alert_count=1,
    severity_max = that alert's severity
  - correlate_alerts: 3 alerts within the time window AND sharing a
    ticker → ONE incident with alert_count=3
  - correlate_alerts: 2 alerts > window apart → TWO incidents (split
    by time even though they share an entity)
  - correlate_alerts: 2 alerts within window but NO shared entity AND
    different alert_types → TWO incidents (split by unrelatedness)
  - correlate_alerts: 2 alerts within window with NO shared entity but
    SAME alert_type → ONE incident (alert_type-only relatedness is
    enough to group)
  - correlate_alerts: severity_max picks the highest severity
    (CRITICAL > HIGH > MEDIUM > LOW)
  - correlate_alerts: dominant_alert_type is the most common; ties are
    broken alphabetically
  - entities_touched aggregates unique tickers/routes/ports across the
    group (dedup + sorted)
  - incident_id is deterministic across runs for the same alert set
  - get_recent_incidents respects window_days (rows older than window
    excluded)
  - get_incident_summary on empty DB → zeroed dict
  - get_incident_summary aggregates correctly on a real fixture
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine.alert_correlator import (
    AlertIncident,
    correlate_alerts,
    get_incident_summary,
    get_recent_incidents,
)
from engine.alert_engine_v2 import ShippingAlert, save_alerts


# ─── Fixture: isolate SQLite per test ──────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Redirect the SQLite state DB to a per-test tmp_path so no test
    touches the real cache/ship_tracker.db. Mirrors the fixture in
    tests/test_alert_analytics.py."""
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Helpers ───────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _mk_alert(
    alert_id: str,
    *,
    alert_type: str = "BDI_MOVE",
    severity: str = "HIGH",
    created_at: datetime | None = None,
    ticker: str = "",
    route_id: str = "",
    port_locode: str = "",
) -> ShippingAlert:
    """Construct a ShippingAlert with the minimum required shape.

    Defaults to a BDI_MOVE / HIGH with no entities — tests that need
    a specific shape override the relevant fields. ``created_at`` is
    passed as a datetime for convenience and serialized to ISO inside
    the helper.
    """
    ts = (created_at if created_at is not None else _now()).isoformat()
    return ShippingAlert(
        alert_id=alert_id,
        created_at=ts,
        alert_type=alert_type,
        severity=severity,
        title=f"title-{alert_id}",
        body=f"body-{alert_id}",
        ticker=ticker,
        route_id=route_id,
        port_locode=port_locode,
        value=0.0,
        threshold=0.0,
        change_pct=0.0,
        acknowledged=False,
    )


# ─── AlertIncident dataclass shape ─────────────────────────────────────────

def test_alert_incident_shape_minimal() -> None:
    inc = AlertIncident(
        incident_id="abc",
        started_at="2026-05-22T00:00:00+00:00",
        severity_max="HIGH",
        alert_count=1,
        dominant_alert_type="BDI_MOVE",
    )
    assert inc.incident_id == "abc"
    assert inc.severity_max == "HIGH"
    assert inc.alert_count == 1
    assert inc.dominant_alert_type == "BDI_MOVE"
    # Defaults for the collection fields.
    assert inc.alerts == []
    assert inc.entities_touched == {}


def test_alert_incident_with_entities_touched() -> None:
    inc = AlertIncident(
        incident_id="abc",
        started_at="2026-05-22T00:00:00+00:00",
        severity_max="CRITICAL",
        alert_count=2,
        dominant_alert_type="STOCK_MOVE",
        alerts=[],
        entities_touched={"tickers": ["ZIM"], "routes": [], "ports": []},
    )
    assert inc.entities_touched["tickers"] == ["ZIM"]
    assert inc.entities_touched["routes"] == []
    assert inc.entities_touched["ports"] == []


# ─── correlate_alerts: trivial cases ───────────────────────────────────────

def test_correlate_alerts_empty_input() -> None:
    assert correlate_alerts([]) == []


def test_correlate_alerts_single_alert_makes_one_incident() -> None:
    a = _mk_alert("a1", severity="MEDIUM", alert_type="STOCK_MOVE", ticker="ZIM")
    incidents = correlate_alerts([a])
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.alert_count == 1
    assert inc.severity_max == "MEDIUM"
    assert inc.dominant_alert_type == "STOCK_MOVE"
    assert inc.alerts[0].alert_id == "a1"


def test_correlate_alerts_single_alert_entities_touched_shape() -> None:
    """A single alert with one entity populates only that entity bucket;
    the other buckets are present but empty (stable shape)."""
    a = _mk_alert("a1", ticker="ZIM")
    inc = correlate_alerts([a])[0]
    assert inc.entities_touched == {"tickers": ["ZIM"], "routes": [], "ports": []}


# ─── correlate_alerts: time + relatedness joining ──────────────────────────

def test_correlate_alerts_three_within_window_shared_ticker_one_incident() -> None:
    """3 alerts in the same 30-min window all touching ZIM should
    collapse into a single incident."""
    base = _now()
    alerts = [
        _mk_alert("a1", created_at=base, ticker="ZIM",
                  alert_type="STOCK_MOVE"),
        _mk_alert("a2", created_at=base + timedelta(minutes=5),
                  ticker="ZIM", alert_type="SIGNAL_FIRE"),
        _mk_alert("a3", created_at=base + timedelta(minutes=20),
                  ticker="ZIM", alert_type="STOCK_MOVE"),
    ]
    incidents = correlate_alerts(alerts, window_minutes=30)
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.alert_count == 3
    assert "ZIM" in inc.entities_touched["tickers"]


def test_correlate_alerts_two_outside_window_split_into_two() -> None:
    """Two alerts more than ``window_minutes`` apart should land in
    separate incidents even when they share an entity."""
    base = _now()
    alerts = [
        _mk_alert("a1", created_at=base, ticker="ZIM"),
        # Far enough out (60 minutes vs the 30-minute default window).
        _mk_alert("a2", created_at=base + timedelta(minutes=60),
                  ticker="ZIM"),
    ]
    incidents = correlate_alerts(alerts, window_minutes=30)
    assert len(incidents) == 2
    # Each incident holds exactly one alert.
    assert all(inc.alert_count == 1 for inc in incidents)


def test_correlate_alerts_unrelated_within_window_split_into_two() -> None:
    """Two alerts inside the window but with NO shared entity AND
    different alert_types should NOT be grouped."""
    base = _now()
    alerts = [
        _mk_alert("a1", created_at=base, ticker="ZIM",
                  alert_type="STOCK_MOVE"),
        _mk_alert("a2", created_at=base + timedelta(minutes=5),
                  port_locode="USLAX", alert_type="CONGESTION"),
    ]
    incidents = correlate_alerts(alerts, window_minutes=30)
    assert len(incidents) == 2


def test_correlate_alerts_same_type_no_entity_joins() -> None:
    """alert_type relatedness alone is sufficient — two BDI_MOVE alerts
    within the window with no entities join into one incident."""
    base = _now()
    alerts = [
        _mk_alert("a1", created_at=base, alert_type="BDI_MOVE"),
        _mk_alert("a2", created_at=base + timedelta(minutes=5),
                  alert_type="BDI_MOVE"),
    ]
    incidents = correlate_alerts(alerts, window_minutes=30)
    assert len(incidents) == 1
    assert incidents[0].alert_count == 2


def test_correlate_alerts_chain_keeps_window_alive() -> None:
    """The window is measured against the LAST member of the open
    incident, not the start — so a chain of related alerts spaced
    under-window each can stretch beyond the start+window mark."""
    base = _now()
    alerts = [
        _mk_alert(f"a{i}", created_at=base + timedelta(minutes=15 * i),
                  ticker="ZIM")
        for i in range(5)  # 0, 15, 30, 45, 60 minutes — all 15min apart
    ]
    incidents = correlate_alerts(alerts, window_minutes=30)
    assert len(incidents) == 1
    assert incidents[0].alert_count == 5


# ─── correlate_alerts: severity_max ────────────────────────────────────────

def test_correlate_alerts_severity_max_picks_critical_over_high() -> None:
    base = _now()
    alerts = [
        _mk_alert("a1", created_at=base, ticker="ZIM", severity="HIGH"),
        _mk_alert("a2", created_at=base + timedelta(minutes=5),
                  ticker="ZIM", severity="CRITICAL"),
        _mk_alert("a3", created_at=base + timedelta(minutes=10),
                  ticker="ZIM", severity="MEDIUM"),
    ]
    inc = correlate_alerts(alerts, window_minutes=30)[0]
    assert inc.severity_max == "CRITICAL"


def test_correlate_alerts_severity_max_picks_high_when_no_critical() -> None:
    base = _now()
    alerts = [
        _mk_alert("a1", created_at=base, ticker="ZIM", severity="MEDIUM"),
        _mk_alert("a2", created_at=base + timedelta(minutes=5),
                  ticker="ZIM", severity="LOW"),
        _mk_alert("a3", created_at=base + timedelta(minutes=10),
                  ticker="ZIM", severity="HIGH"),
    ]
    inc = correlate_alerts(alerts, window_minutes=30)[0]
    assert inc.severity_max == "HIGH"


# ─── correlate_alerts: dominant_alert_type ─────────────────────────────────

def test_correlate_alerts_dominant_alert_type_picks_most_common() -> None:
    base = _now()
    alerts = [
        _mk_alert("a1", created_at=base, ticker="ZIM",
                  alert_type="STOCK_MOVE"),
        _mk_alert("a2", created_at=base + timedelta(minutes=5),
                  ticker="ZIM", alert_type="STOCK_MOVE"),
        _mk_alert("a3", created_at=base + timedelta(minutes=10),
                  ticker="ZIM", alert_type="SIGNAL_FIRE"),
    ]
    inc = correlate_alerts(alerts, window_minutes=30)[0]
    assert inc.dominant_alert_type == "STOCK_MOVE"


def test_correlate_alerts_dominant_alert_type_ties_broken_alphabetically() -> None:
    """When 2 alert_types are tied for highest count, the alphabetically
    earliest wins (deterministic across runs)."""
    base = _now()
    alerts = [
        _mk_alert("a1", created_at=base, ticker="ZIM",
                  alert_type="STOCK_MOVE"),
        _mk_alert("a2", created_at=base + timedelta(minutes=5),
                  ticker="ZIM", alert_type="BDI_MOVE"),
    ]
    inc = correlate_alerts(alerts, window_minutes=30)[0]
    # BDI_MOVE < STOCK_MOVE alphabetically.
    assert inc.dominant_alert_type == "BDI_MOVE"


# ─── correlate_alerts: entities_touched ────────────────────────────────────

def test_correlate_alerts_entities_touched_aggregates_unique_sorted() -> None:
    base = _now()
    alerts = [
        _mk_alert("a1", created_at=base, ticker="ZIM",
                  route_id="transpacific_eb"),
        _mk_alert("a2", created_at=base + timedelta(minutes=2),
                  ticker="MATX", route_id="transpacific_eb",
                  port_locode="USLAX"),
        _mk_alert("a3", created_at=base + timedelta(minutes=4),
                  ticker="ZIM",  # duplicate ticker — should dedup
                  port_locode="CNSHA"),
    ]
    inc = correlate_alerts(alerts, window_minutes=30)[0]
    assert inc.entities_touched["tickers"] == ["MATX", "ZIM"]  # sorted+unique
    assert inc.entities_touched["routes"] == ["transpacific_eb"]
    assert inc.entities_touched["ports"] == ["CNSHA", "USLAX"]  # sorted


def test_correlate_alerts_entities_touched_stable_keys_when_empty() -> None:
    """All three entity buckets are present even when none are touched."""
    a = _mk_alert("a1", alert_type="MACRO")  # no entity fields
    inc = correlate_alerts([a])[0]
    assert set(inc.entities_touched.keys()) == {"tickers", "routes", "ports"}
    assert inc.entities_touched["tickers"] == []
    assert inc.entities_touched["routes"] == []
    assert inc.entities_touched["ports"] == []


# ─── correlate_alerts: incident_id determinism ─────────────────────────────

def test_correlate_alerts_incident_id_deterministic_across_runs() -> None:
    """Same alert set → same incident_id, regardless of input order."""
    base = _now()
    a1 = _mk_alert("a1", created_at=base, ticker="ZIM")
    a2 = _mk_alert("a2", created_at=base + timedelta(minutes=5),
                   ticker="ZIM")
    a3 = _mk_alert("a3", created_at=base + timedelta(minutes=10),
                   ticker="ZIM")

    inc_a = correlate_alerts([a1, a2, a3])[0]
    inc_b = correlate_alerts([a3, a1, a2])[0]  # different input order
    assert inc_a.incident_id == inc_b.incident_id


def test_correlate_alerts_incident_id_changes_with_membership() -> None:
    """Adding a new alert into the incident changes the id."""
    base = _now()
    a1 = _mk_alert("a1", created_at=base, ticker="ZIM")
    a2 = _mk_alert("a2", created_at=base + timedelta(minutes=5),
                   ticker="ZIM")
    a3 = _mk_alert("a3", created_at=base + timedelta(minutes=10),
                   ticker="ZIM")

    inc_two = correlate_alerts([a1, a2])[0]
    inc_three = correlate_alerts([a1, a2, a3])[0]
    assert inc_two.incident_id != inc_three.incident_id


# ─── correlate_alerts: never raises ────────────────────────────────────────

def test_correlate_alerts_bad_timestamp_skipped_not_raised() -> None:
    """An alert with an unparseable created_at is skipped, not crashed."""
    good = _mk_alert("good", ticker="ZIM")
    bad = ShippingAlert(
        alert_id="bad", created_at="not-a-date", alert_type="BDI_MOVE",
        severity="HIGH", title="t", body="b", ticker="", route_id="",
        port_locode="", value=0.0, threshold=0.0, change_pct=0.0,
        acknowledged=False,
    )
    incidents = correlate_alerts([good, bad])
    # The good alert still produces an incident; the bad one is dropped
    # but nothing crashes.
    assert any(inc.alert_count == 1 and inc.alerts[0].alert_id == "good"
               for inc in incidents)


# ─── get_recent_incidents: window_days + DB integration ────────────────────

def test_get_recent_incidents_respects_window_days() -> None:
    """Alerts older than ``window_days`` are excluded."""
    base = _now()
    # Persist one recent alert and one ancient one.
    recent = _mk_alert("recent", created_at=base, ticker="ZIM")
    ancient = _mk_alert(
        "ancient",
        created_at=base - timedelta(days=14),
        ticker="MATX",
    )
    save_alerts([recent, ancient], user_id="")

    incidents = get_recent_incidents(window_days=7, user_id="")
    # Only the recent one survives the window filter.
    all_ids = {a.alert_id for inc in incidents for a in inc.alerts}
    assert "recent" in all_ids
    assert "ancient" not in all_ids


def test_get_recent_incidents_empty_db() -> None:
    assert get_recent_incidents(window_days=7, user_id="") == []


def test_get_recent_incidents_sorted_newest_first() -> None:
    """When there are multiple incidents, they sort by started_at desc."""
    base = _now()
    # Two unrelated incidents separated by 2 hours.
    older = _mk_alert("older", created_at=base - timedelta(hours=3),
                      ticker="ZIM", alert_type="STOCK_MOVE")
    newer = _mk_alert("newer", created_at=base, ticker="MATX",
                      alert_type="CONGESTION")
    save_alerts([older, newer], user_id="")
    incidents = get_recent_incidents(window_days=7, user_id="")
    assert len(incidents) == 2
    # First is newest.
    assert incidents[0].started_at >= incidents[1].started_at


def test_get_recent_incidents_nonpositive_window_returns_empty() -> None:
    """window_days <= 0 short-circuits — without this guard the cutoff
    would slide forward in time and silently mask every row."""
    a = _mk_alert("a1", ticker="ZIM")
    save_alerts([a], user_id="")
    assert get_recent_incidents(window_days=0, user_id="") == []


# ─── get_incident_summary ──────────────────────────────────────────────────

def test_get_incident_summary_empty_returns_zeroed_dict() -> None:
    s = get_incident_summary(window_days=7)
    assert s == {
        "n_incidents": 0,
        "n_total_alerts": 0,
        "avg_alerts_per_incident": 0.0,
        "largest_incident_size": 0,
        "breakdown_by_dominant_type": {},
    }


def test_get_incident_summary_aggregates_correctly() -> None:
    """Two distinct incidents: one with 3 STOCK_MOVE alerts on distinct
    tickers (grouped by alert_type relatedness), one with 1 CONGESTION
    alert. Summary should reflect both.

    Each STOCK_MOVE alert uses a different ticker so the v14
    window-dedup inside save_alerts (keyed on
    alert_type+severity+ticker+route+port) does not collapse them
    into a single row before they reach the correlator.
    """
    base = _now()
    # Incident 1: 3 STOCK_MOVE alerts on DIFFERENT tickers, within
    # 30 minutes. correlate_alerts groups them via the shared
    # alert_type ("STOCK_MOVE"), even though no entity overlaps.
    stock_tickers = ["ZIM", "MATX", "SBLK"]
    g1 = [
        _mk_alert(f"s{i}", created_at=base - timedelta(minutes=i * 5),
                  ticker=stock_tickers[i], alert_type="STOCK_MOVE")
        for i in range(3)
    ]
    # Incident 2: 1 CONGESTION alert at USLAX, hours apart from incident 1.
    g2 = [
        _mk_alert("c1", created_at=base - timedelta(hours=5),
                  port_locode="USLAX", alert_type="CONGESTION"),
    ]
    save_alerts(g1 + g2, user_id="")

    s = get_incident_summary(window_days=7)
    assert s["n_incidents"] == 2
    assert s["n_total_alerts"] == 4
    assert s["largest_incident_size"] == 3
    assert s["avg_alerts_per_incident"] == pytest.approx(2.0)
    # breakdown counts INCIDENTS, not alerts — one STOCK_MOVE-dominated
    # incident, one CONGESTION-dominated incident.
    assert s["breakdown_by_dominant_type"] == {
        "STOCK_MOVE": 1,
        "CONGESTION": 1,
    }
