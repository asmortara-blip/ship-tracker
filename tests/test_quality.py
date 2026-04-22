"""Unit tests for data.quality — DataSource and DataSeries primitives."""
from __future__ import annotations


import pytest

from data.quality import (
    DataKind, DataQuality, DataSeries, DataSource, quality_color, wrap,
)


class TestDataSource:
    def test_live_constructor_sets_good_quality(self) -> None:
        ds = DataSource.live("FRED", url="https://fred.stlouisfed.org", sla_hours=24)
        assert ds.name == "FRED"
        assert ds.kind == DataKind.LIVE
        assert ds.quality == DataQuality.GOOD
        assert ds.sla_hours == 24
        assert ds.within_sla is True

    def test_cached_downgrades_to_stale_past_sla(self) -> None:
        ds = DataSource.cached("Baltic Exchange", age_hours=48, sla_hours=24)
        assert ds.kind == DataKind.CACHED
        assert ds.quality == DataQuality.STALE

    def test_cached_stays_good_within_sla(self) -> None:
        ds = DataSource.cached("Baltic Exchange", age_hours=12, sla_hours=24)
        assert ds.quality == DataQuality.GOOD

    def test_scraped_is_unofficial(self) -> None:
        ds = DataSource.scraped("Freightos", url="https://fbx.freightos.com")
        assert ds.kind == DataKind.SCRAPED
        assert ds.quality == DataQuality.UNOFFICIAL

    def test_modeled_and_demo_have_expected_kinds(self) -> None:
        assert DataSource.modeled("forward curve").quality == DataQuality.MODELED
        assert DataSource.demo().quality == DataQuality.DEMO

    def test_age_hours_none_when_no_timestamp(self) -> None:
        ds = DataSource(name="x", as_of=None)
        assert ds.age_hours is None

    def test_age_hours_is_recent_for_just_now(self) -> None:
        ds = DataSource.live("x")
        assert ds.age_hours is not None
        assert ds.age_hours < 1.0  # under an hour old

    def test_with_age_downgrades_beyond_sla(self) -> None:
        ds = DataSource.live("x", sla_hours=6)
        stale = ds.with_age(12)
        assert stale.quality == DataQuality.STALE
        assert stale.kind == DataKind.CACHED

    def test_with_age_noop_within_sla(self) -> None:
        ds = DataSource.live("x", sla_hours=6)
        fresh = ds.with_age(3)
        assert fresh is ds  # no downgrade → same object

    def test_to_dict_serializes_timestamp(self) -> None:
        ds = DataSource.live("x")
        d = ds.to_dict()
        assert isinstance(d["as_of"], str)

    def test_color_maps_quality(self) -> None:
        assert quality_color(DataQuality.GOOD) != quality_color(DataQuality.DEMO)
        assert DataSource.live("x").color == quality_color(DataQuality.GOOD)

    def test_frozen(self) -> None:
        ds = DataSource.live("x")
        with pytest.raises(Exception):
            ds.name = "y"  # type: ignore[misc]


class TestDataSeries:
    def test_truthy_when_data_present(self) -> None:
        import pandas as pd
        s = DataSeries(data=pd.Series([1, 2]), source=DataSource.live("x"))
        assert bool(s) is True

    def test_falsy_when_data_none(self) -> None:
        s = DataSeries(data=None, source=DataSource.live("x"))
        assert bool(s) is False

    def test_falsy_when_dataframe_empty(self) -> None:
        import pandas as pd
        s = DataSeries(data=pd.DataFrame(), source=DataSource.live("x"))
        assert bool(s) is False

    def test_unwrap_returns_payload(self) -> None:
        payload = {"a": 1}
        s = DataSeries(data=payload, source=DataSource.live("x"))
        assert s.unwrap() is payload

    def test_wrap_shorthand(self) -> None:
        ds = DataSource.live("x")
        s = wrap([1, 2, 3], ds, extra="meta")
        assert s.source is ds
        assert s.meta == {"extra": "meta"}
