"""Unit tests for state.session — typed SessionState."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from state.session import (
    Filters,
    SessionState,
    apply_filters_to_freight,
    apply_filters_to_stock,
    as_dict,
)


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


# ─── apply_filters_to_freight ──────────────────────────────────────────────


def _rate_df(route_id: str, n: int = 90) -> pd.DataFrame:
    """Tiny synthetic freight DataFrame for filter tests."""
    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=n, freq="D"),
        "rate_usd_per_feu": [2000.0 + i for i in range(n)],
        "route_id": [route_id] * n,
    })


def test_apply_freight_empty_input_returns_empty_dict() -> None:
    assert apply_filters_to_freight({}, Filters()) == {}
    assert apply_filters_to_freight(None, Filters()) == {}


def test_apply_freight_identity_when_filters_default() -> None:
    """No date bounds, no route filter → return the same routes unchanged."""
    freight = {
        "transpacific_eb": _rate_df("transpacific_eb"),
        "asia_europe":     _rate_df("asia_europe"),
    }
    out = apply_filters_to_freight(freight, Filters())
    assert set(out.keys()) == set(freight.keys())
    # Row counts preserved when no date filter.
    for key in freight:
        assert len(out[key]) == len(freight[key])


def test_apply_freight_route_filter_narrows_keys() -> None:
    freight = {
        "transpacific_eb": _rate_df("transpacific_eb"),
        "asia_europe":     _rate_df("asia_europe"),
        "med_hub_to_asia": _rate_df("med_hub_to_asia"),
    }
    filters = Filters(routes=("transpacific_eb", "asia_europe"))
    out = apply_filters_to_freight(freight, filters)
    assert set(out.keys()) == {"transpacific_eb", "asia_europe"}


def test_apply_freight_date_window_truncates_rows() -> None:
    freight = {"transpacific_eb": _rate_df("transpacific_eb", n=180)}
    # 180-day series starting 2025-01-01 → 2025-06-29 inclusive
    filters = Filters(
        date_start=date(2025, 2, 1),
        date_end=date(2025, 3, 31),
    )
    out = apply_filters_to_freight(freight, filters)
    trimmed = out["transpacific_eb"]
    assert len(trimmed) > 0
    assert pd.Timestamp(trimmed["date"].min()) >= pd.Timestamp(date(2025, 2, 1))
    assert pd.Timestamp(trimmed["date"].max()) <= pd.Timestamp(date(2025, 3, 31))


def test_apply_freight_combines_route_and_date_filters() -> None:
    freight = {
        "transpacific_eb": _rate_df("transpacific_eb", n=180),
        "asia_europe":     _rate_df("asia_europe", n=180),
        "med_hub_to_asia": _rate_df("med_hub_to_asia", n=180),
    }
    filters = Filters(
        routes=("transpacific_eb",),
        date_start=date(2025, 3, 1),
    )
    out = apply_filters_to_freight(freight, filters)
    assert set(out.keys()) == {"transpacific_eb"}
    assert pd.Timestamp(out["transpacific_eb"]["date"].min()) >= pd.Timestamp(date(2025, 3, 1))


def test_apply_freight_handles_dataframe_without_date_column() -> None:
    """Frames missing the `date` column are passed through unchanged."""
    odd = pd.DataFrame({"rate_usd_per_feu": [2000, 2100], "other": [1, 2]})
    freight = {"weird_route": odd}
    filters = Filters(date_start=date(2025, 1, 1), date_end=date(2025, 2, 1))
    out = apply_filters_to_freight(freight, filters)
    # Same length — date filter is a no-op here.
    assert len(out["weird_route"]) == 2


# ─── apply_filters_to_stock ────────────────────────────────────────────────


def _stock_df(ticker: str, n: int = 90) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=n, freq="B"),
        "close": [15.0 + i * 0.1 for i in range(n)],
        "ticker": [ticker] * n,
    })


def test_apply_stock_empty_input() -> None:
    assert apply_filters_to_stock({}, Filters()) == {}
    assert apply_filters_to_stock(None, Filters()) == {}


def test_apply_stock_universe_filter() -> None:
    stock = {"ZIM": _stock_df("ZIM"), "MATX": _stock_df("MATX"),
             "SBLK": _stock_df("SBLK")}
    filters = Filters(universe=("ZIM", "MATX"))
    out = apply_filters_to_stock(stock, filters)
    assert set(out.keys()) == {"ZIM", "MATX"}


def test_apply_stock_date_truncation_per_ticker() -> None:
    stock = {"ZIM": _stock_df("ZIM", n=180)}
    filters = Filters(
        date_start=date(2025, 4, 1),
        date_end=date(2025, 5, 31),
    )
    out = apply_filters_to_stock(stock, filters)
    trimmed = out["ZIM"]
    assert len(trimmed) > 0
    assert pd.Timestamp(trimmed["date"].min()) >= pd.Timestamp(date(2025, 4, 1))
    assert pd.Timestamp(trimmed["date"].max()) <= pd.Timestamp(date(2025, 5, 31))


def test_apply_stock_passes_through_dict_snapshot_values() -> None:
    """A dict-valued stock_data entry (price snapshot, no time series) has
    no date dimension to truncate — it should pass through unchanged."""
    stock = {
        "ZIM":  _stock_df("ZIM", n=90),
        "MATX": {"price": 94.0, "change_pct": -0.5},   # dict snapshot
    }
    filters = Filters(
        universe=("ZIM", "MATX"),
        date_start=date(2025, 4, 1),
    )
    out = apply_filters_to_stock(stock, filters)
    # ZIM truncated; MATX dict passed through.
    assert isinstance(out["MATX"], dict)
    assert out["MATX"]["price"] == 94.0
    assert pd.Timestamp(out["ZIM"]["date"].min()) >= pd.Timestamp(date(2025, 4, 1))
