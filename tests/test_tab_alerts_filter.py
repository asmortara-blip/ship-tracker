"""Unit tests for ``ui.tab_alerts._apply_active_filter`` — the pure-function
helper that filters an alerts list against the active filter payload
persisted by the Saved Filters panel.

The helper accepts BOTH dict-shaped alerts (the live-derived alerts the
Active Alerts panel renders) AND ShippingAlert dataclass instances (the
persisted alerts loaded via ``load_alerts`` and fed into the incidents
correlator + ack-analytics aggregator). We test with the dataclass shape
where it matters (severity / alert_type / ticker / acknowledged /
created_at), since that is the shape the prompt's standard-key contract
was designed against.

Defining properties under test
------------------------------
* Empty / None payload → input returned unchanged (no-op semantics).
* ``severity=HIGH`` → keeps HIGH and CRITICAL only.
* ``ticker`` → exact string match.
* ``alert_type`` → exact string match.
* ``acknowledged=True / False`` → boolean partition.
* ``window_hours=24`` → only alerts within the last 24h.
* Malformed severity ('BANANA') → predicate is silently dropped, the
  rest of the list is unaffected.
* Empty alert list + non-empty payload → returns [].

Pure function, no DB / Streamlit / network — fast tests.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine.alert_engine_v2 import ShippingAlert
from ui.tab_alerts import _apply_active_filter


# ─── Helpers ───────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _mk_alert(
    *,
    alert_id: str = "a1",
    created_at: datetime | None = None,
    alert_type: str = "BDI_MOVE",
    severity: str = "HIGH",
    ticker: str = "",
    acknowledged: bool = False,
) -> ShippingAlert:
    ts = (created_at if created_at is not None else _now()).isoformat()
    return ShippingAlert(
        alert_id=alert_id,
        created_at=ts,
        alert_type=alert_type,
        severity=severity,
        title=f"title-{alert_id}",
        body=f"body-{alert_id}",
        ticker=ticker,
        route_id="",
        port_locode="",
        value=0.0,
        threshold=0.0,
        change_pct=0.0,
        acknowledged=acknowledged,
    )


@pytest.fixture
def sample_alerts() -> list[ShippingAlert]:
    """A mixed list spanning every severity, type, ticker, and ack state."""
    base = _now()
    return [
        _mk_alert(alert_id="a1", severity="CRITICAL", alert_type="BDI_MOVE",
                  ticker="ZIM", acknowledged=True),
        _mk_alert(alert_id="a2", severity="HIGH", alert_type="STOCK_MOVE",
                  ticker="ZIM", acknowledged=False),
        _mk_alert(alert_id="a3", severity="MEDIUM", alert_type="MACRO",
                  ticker="MATX", acknowledged=False),
        _mk_alert(alert_id="a4", severity="LOW", alert_type="EVENT",
                  ticker="SBLK", acknowledged=True),
        _mk_alert(alert_id="a5", severity="HIGH", alert_type="BDI_MOVE",
                  ticker="MATX", acknowledged=False,
                  created_at=base - timedelta(hours=48)),  # outside 24h
    ]


# ─── Tests ─────────────────────────────────────────────────────────────────

def test_empty_payload_returns_all(sample_alerts: list[ShippingAlert]) -> None:
    """An empty dict payload is a no-op."""
    out = _apply_active_filter(sample_alerts, {})
    assert len(out) == len(sample_alerts)
    # Ensure it's a list (not the original) so callers can mutate freely.
    assert out is not sample_alerts


def test_none_payload_returns_input_unchanged(
    sample_alerts: list[ShippingAlert],
) -> None:
    """A None payload is a no-op."""
    out = _apply_active_filter(sample_alerts, None)
    assert len(out) == len(sample_alerts)
    assert [a.alert_id for a in out] == [a.alert_id for a in sample_alerts]


def test_empty_alert_list_returns_empty_list() -> None:
    """No alerts + an active payload → empty list (not a crash)."""
    out = _apply_active_filter([], {"severity": "HIGH"})
    assert out == []


def test_severity_high_keeps_high_and_critical(
    sample_alerts: list[ShippingAlert],
) -> None:
    """``severity=HIGH`` keeps rows with rank ≥ HIGH (HIGH + CRITICAL)."""
    out = _apply_active_filter(sample_alerts, {"severity": "HIGH"})
    severities = sorted({a.severity for a in out})
    assert severities == ["CRITICAL", "HIGH"]
    assert all(a.severity in ("CRITICAL", "HIGH") for a in out)


def test_severity_critical_keeps_only_critical(
    sample_alerts: list[ShippingAlert],
) -> None:
    """``severity=CRITICAL`` keeps the top tier only."""
    out = _apply_active_filter(sample_alerts, {"severity": "CRITICAL"})
    assert all(a.severity == "CRITICAL" for a in out)
    assert len(out) == 1


def test_ticker_exact_match(sample_alerts: list[ShippingAlert]) -> None:
    """``ticker=ZIM`` keeps only ZIM rows."""
    out = _apply_active_filter(sample_alerts, {"ticker": "ZIM"})
    assert len(out) == 2
    assert all(a.ticker == "ZIM" for a in out)


def test_alert_type_bdi_move(sample_alerts: list[ShippingAlert]) -> None:
    """``alert_type='BDI_MOVE'`` keeps only that type."""
    out = _apply_active_filter(sample_alerts, {"alert_type": "BDI_MOVE"})
    assert all(a.alert_type == "BDI_MOVE" for a in out)
    assert len(out) == 2


def test_acknowledged_true(sample_alerts: list[ShippingAlert]) -> None:
    """``acknowledged=True`` keeps only ack'd rows."""
    out = _apply_active_filter(sample_alerts, {"acknowledged": True})
    assert all(a.acknowledged is True for a in out)
    assert len(out) == 2


def test_acknowledged_false(sample_alerts: list[ShippingAlert]) -> None:
    """``acknowledged=False`` keeps only un-ack'd rows."""
    out = _apply_active_filter(sample_alerts, {"acknowledged": False})
    assert all(a.acknowledged is False for a in out)
    assert len(out) == 3


def test_window_hours_24_drops_older(
    sample_alerts: list[ShippingAlert],
) -> None:
    """``window_hours=24`` drops the synthetic 48h-old row."""
    out = _apply_active_filter(sample_alerts, {"window_hours": 24})
    ids = {a.alert_id for a in out}
    assert "a5" not in ids  # the 48h-old one
    assert len(out) == 4


def test_malformed_severity_silently_drops_predicate(
    sample_alerts: list[ShippingAlert],
) -> None:
    """``severity='BANANA'`` (not in the rank table) drops the predicate
    and returns the rest of the list unfiltered — matches the docstring
    contract that one bad key must not blow up the rest of the filter."""
    out = _apply_active_filter(sample_alerts, {"severity": "BANANA"})
    assert len(out) == len(sample_alerts)


def test_combined_predicates_intersect(
    sample_alerts: list[ShippingAlert],
) -> None:
    """Multiple keys AND together (every predicate must hold)."""
    out = _apply_active_filter(
        sample_alerts,
        {"severity": "HIGH", "ticker": "ZIM"},
    )
    # Only a1 (CRITICAL, ZIM) + a2 (HIGH, ZIM) qualify.
    assert {a.alert_id for a in out} == {"a1", "a2"}


def test_unrecognized_key_is_silently_ignored(
    sample_alerts: list[ShippingAlert],
) -> None:
    """Forward-compat: API-driven payloads may include keys this commit
    doesn't honor yet — they must NOT crash the helper."""
    out = _apply_active_filter(
        sample_alerts,
        {"some_future_key": "whatever", "ticker": "ZIM"},
    )
    # The ticker predicate still applies; the unknown key is dropped.
    assert all(a.ticker == "ZIM" for a in out)


def test_dict_shaped_alerts_use_same_field_access() -> None:
    """The helper handles dict-shaped alerts too (the active-alerts list
    in tab_alerts is a list[dict])."""
    base = _now()
    dict_alerts = [
        {"severity": "CRITICAL", "ticker": "ZIM", "acknowledged": False,
         "triggered_at": base.isoformat()},
        {"severity": "MEDIUM", "ticker": "MATX", "acknowledged": True,
         "triggered_at": base.isoformat()},
    ]
    out = _apply_active_filter(dict_alerts, {"ticker": "ZIM"})
    assert len(out) == 1
    assert out[0]["ticker"] == "ZIM"
