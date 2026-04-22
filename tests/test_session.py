"""Unit tests for state.session — typed SessionState."""
from __future__ import annotations

from datetime import date, timedelta

from state.session import SessionState, Filters, as_dict


def test_filters_default_shape() -> None:
    f = Filters()
    assert f.date_start is None
    assert f.demo_mode is False
    assert f.universe == ()


def test_session_state_defaults() -> None:
    s = SessionState()
    assert s.nav_section == "dashboard"
    assert s.selected_entity is None
    assert s.alert_rules == []
    assert s.scenario_overlays == {}


def test_session_state_is_mutable() -> None:
    s = SessionState()
    s.selected_entity = "ZIM"
    s.alert_rules.append({"rule": "bdi > 2000"})
    assert s.selected_entity == "ZIM"
    assert len(s.alert_rules) == 1


def test_filters_accept_typed_tuples() -> None:
    today = date.today()
    f = Filters(
        date_start=today - timedelta(days=30),
        date_end=today,
        universe=("ZIM", "SBLK"),
    )
    assert len(f.universe) == 2
    assert f.date_end == today


def test_as_dict_round_trips_filters() -> None:
    s = SessionState(filters=Filters(universe=("ZIM",)))
    d = as_dict(s)
    assert "filters" in d
    assert d["filters"]["universe"] == ("ZIM",)
