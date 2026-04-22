"""Unit tests for ui.styles additions — live_data_badge, source_footer, spark_cell, regime_pill."""
from __future__ import annotations

from data.quality import DataSource, DataKind, DataQuality
from ui.styles import (
    live_data_badge, source_footer, spark_cell, regime_pill, C_HIGH, C_LOW,
)


class TestLiveDataBadge:
    def test_renders_datasource(self) -> None:
        ds = DataSource.live("FRED", url="https://fred.stlouisfed.org")
        html = live_data_badge(ds)
        assert "LIVE" in html
        assert "FRED" in html
        assert "pulse-dot" in html

    def test_renders_from_kwargs(self) -> None:
        html = live_data_badge("Baltic", kind=DataKind.SCRAPED,
                                quality=DataQuality.UNOFFICIAL)
        assert "SCRAPED" in html
        assert "Baltic" in html

    def test_demo_uses_red_palette(self) -> None:
        ds = DataSource.demo()
        html = live_data_badge(ds)
        assert C_LOW.lower() in html.lower()

    def test_good_uses_green_palette(self) -> None:
        ds = DataSource.live("x")
        html = live_data_badge(ds)
        assert C_HIGH.lower() in html.lower()

    def test_includes_tooltip(self) -> None:
        ds = DataSource.live("FRED")
        html = live_data_badge(ds)
        assert "title=" in html


class TestSourceFooter:
    def test_empty_sources_returns_empty_string(self) -> None:
        assert source_footer([]) == ""

    def test_renders_multiple_sources(self) -> None:
        sources = [
            DataSource.live("FRED"),
            DataSource.scraped("Baltic", url="https://www.balticexchange.com"),
        ]
        html = source_footer(sources)
        assert "FRED" in html
        assert "Baltic" in html
        assert "Source" in html

    def test_accepts_dicts(self) -> None:
        html = source_footer([{"name": "Custom", "kind": "modeled", "quality": "modeled"}])
        assert "Custom" in html
        assert "MODELED" in html


class TestSparkCell:
    def test_short_series_renders_placeholder(self) -> None:
        html = spark_cell([])
        assert "svg" not in html.lower()

    def test_renders_svg_for_real_series(self) -> None:
        html = spark_cell([1.0, 2.0, 1.5, 3.0, 2.5])
        assert "<svg" in html
        assert "polyline" in html

    def test_uptrend_uses_green(self) -> None:
        html = spark_cell([1.0, 2.0, 3.0])
        assert C_HIGH.lower() in html.lower()

    def test_downtrend_uses_red(self) -> None:
        html = spark_cell([3.0, 2.0, 1.0])
        assert C_LOW.lower() in html.lower()

    def test_handles_nan(self) -> None:
        html = spark_cell([1.0, float("nan"), 2.0, 3.0])
        assert "<svg" in html


class TestRegimePill:
    def test_known_regime(self) -> None:
        html = regime_pill("Boom")
        assert "Boom" in html
        assert C_HIGH.lower() in html.lower()

    def test_unknown_regime_uses_fallback_color(self) -> None:
        html = regime_pill("Weird")
        assert "Weird" in html

    def test_mapping_override(self) -> None:
        html = regime_pill("Custom", mapping={"Custom": C_LOW})
        assert C_LOW.lower() in html.lower()
