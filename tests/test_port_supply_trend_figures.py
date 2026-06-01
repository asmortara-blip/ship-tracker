"""Defining-property tests for ui/plots/port_supply_trends.py.

The trend-figure builders are pure plotly — no streamlit imports — so
these tests instantiate ``PortTrendPoint`` fixtures directly without
having to plant snapshot dirs on disk.

Tested defining properties:
  * Empty input → still returns a valid Figure with a "no snapshot
    history yet" annotation (no crash, no exception).
  * Non-empty input → exactly 1 line trace (the deficit-day line).
  * Severity-band shading + the y=0 reference line both exist as
    layout shapes (5 bands + 1 reference line = 6 shapes minimum).
  * Regional figure has the same shape contract as the per-port figure.
"""
from __future__ import annotations

import math

import plotly.graph_objects as go
import pytest

from processing.port_supply_trend import PortTrendPoint
from ui.plots.port_supply_trends import (
    SEVERITY_BANDS,
    build_port_trend_figure,
    build_regional_trend_figure,
)


# ── 1. Empty-input fallback ──────────────────────────────────────────────


def test_port_trend_figure_empty_input_returns_annotated_figure() -> None:
    fig = build_port_trend_figure([], "CNSHA", "Shanghai")
    assert isinstance(fig, go.Figure)
    # "No snapshot history yet" annotation must be present.
    assert any(
        "No snapshot" in (a.text or "")
        for a in fig.layout.annotations
    )


def test_regional_trend_figure_empty_input_returns_annotated_figure() -> None:
    fig = build_regional_trend_figure([], "Asia East")
    assert isinstance(fig, go.Figure)
    assert any(
        "No snapshot" in (a.text or "")
        for a in fig.layout.annotations
    )


# ── 2. Trace count — exactly one line per builder ────────────────────────


def test_port_trend_figure_has_single_line_trace() -> None:
    """One Scatter trace = the deficit-day line. Severity bands and the
    reference line live as layout *shapes*, not traces, so the legend
    stays uncluttered."""
    pts = [
        PortTrendPoint(date="2026-05-24", locode="CNSHA",
                       deficit_days=-2.0, severity_label="Balanced"),
        PortTrendPoint(date="2026-05-25", locode="CNSHA",
                       deficit_days=-4.0, severity_label="Deficit"),
        PortTrendPoint(date="2026-05-26", locode="CNSHA",
                       deficit_days=-6.0, severity_label="Deficit"),
    ]
    fig = build_port_trend_figure(pts, "CNSHA", "Shanghai")
    assert len(fig.data) == 1
    assert fig.data[0].type == "scatter"
    assert fig.data[0].mode == "lines+markers"


def test_regional_trend_figure_has_single_line_trace() -> None:
    series = [
        ("2026-05-24", -1.0),
        ("2026-05-25", -1.5),
        ("2026-05-26", -2.0),
    ]
    fig = build_regional_trend_figure(series, "Asia East")
    assert len(fig.data) == 1
    assert fig.data[0].type == "scatter"


# ── 3. Severity-band shading + reference line as layout shapes ───────────


def test_port_trend_figure_renders_severity_band_shapes() -> None:
    """Five severity bands shaded as rectangles + one zero-crossing
    reference line = 6 layout shapes minimum."""
    pts = [
        PortTrendPoint(date="2026-05-24", locode="CNSHA",
                       deficit_days=-2.0, severity_label="Balanced"),
        PortTrendPoint(date="2026-05-25", locode="CNSHA",
                       deficit_days=-4.0, severity_label="Deficit"),
    ]
    fig = build_port_trend_figure(pts, "CNSHA", "Shanghai")
    shapes = list(fig.layout.shapes)
    # 5 severity bands (rect) + 1 reference line
    rect_shapes = [s for s in shapes if s.type == "rect"]
    line_shapes = [s for s in shapes if s.type == "line"]
    assert len(rect_shapes) == len(SEVERITY_BANDS), (
        f"expected {len(SEVERITY_BANDS)} severity-band rects, "
        f"got {len(rect_shapes)}"
    )
    assert len(line_shapes) >= 1, "expected at least one reference line"


def test_port_trend_figure_reference_line_at_y_zero() -> None:
    """The reference line must sit at y=0 — the deficit/surplus
    crossover the operator looks for at a glance."""
    pts = [
        PortTrendPoint(date="2026-05-25", locode="CNSHA",
                       deficit_days=0.0, severity_label="Balanced"),
    ]
    fig = build_port_trend_figure(pts, "CNSHA", "Shanghai")
    line_shapes = [s for s in fig.layout.shapes if s.type == "line"]
    assert any(s.y0 == 0.0 and s.y1 == 0.0 for s in line_shapes)


def test_regional_trend_figure_renders_severity_band_shapes() -> None:
    series = [("2026-05-25", -2.0), ("2026-05-26", -3.0)]
    fig = build_regional_trend_figure(series, "Asia East")
    shapes = list(fig.layout.shapes)
    rect_shapes = [s for s in shapes if s.type == "rect"]
    line_shapes = [s for s in shapes if s.type == "line"]
    assert len(rect_shapes) == len(SEVERITY_BANDS)
    assert len(line_shapes) >= 1


# ── 4. Data fidelity — values flow into the trace ────────────────────────


def test_port_trend_figure_passes_through_dates_and_deficits() -> None:
    pts = [
        PortTrendPoint(date="2026-05-24", locode="CNSHA",
                       deficit_days=-2.0, severity_label="Balanced"),
        PortTrendPoint(date="2026-05-25", locode="CNSHA",
                       deficit_days=-4.5, severity_label="Deficit"),
    ]
    fig = build_port_trend_figure(pts, "CNSHA", "Shanghai")
    xs = list(fig.data[0].x)
    ys = list(fig.data[0].y)
    assert xs == ["2026-05-24", "2026-05-25"]
    assert ys == [-2.0, -4.5]


def test_port_trend_figure_nan_slot_preserved_for_gap_visualization() -> None:
    """A NaN deficit slot must flow into the y array as NaN so plotly
    breaks the line on the gap day rather than interpolating across it.
    The trace must also have connectgaps=False so the break renders."""
    pts = [
        PortTrendPoint(date="2026-05-24", locode="CNSHA",
                       deficit_days=-2.0, severity_label="Balanced"),
        PortTrendPoint(date="2026-05-25", locode="CNSHA",
                       deficit_days=float("nan"), severity_label=""),
        PortTrendPoint(date="2026-05-26", locode="CNSHA",
                       deficit_days=-4.0, severity_label="Deficit"),
    ]
    fig = build_port_trend_figure(pts, "CNSHA", "Shanghai")
    ys = list(fig.data[0].y)
    assert len(ys) == 3
    assert math.isnan(ys[1])
    # connectgaps=False makes the NaN visually break the line.
    assert fig.data[0].connectgaps is False


# ── 5. Title threading ───────────────────────────────────────────────────


def test_port_trend_figure_title_includes_port_name_and_locode() -> None:
    pts = [
        PortTrendPoint(date="2026-05-25", locode="CNSHA",
                       deficit_days=0.0, severity_label="Balanced"),
    ]
    fig = build_port_trend_figure(pts, "CNSHA", "Shanghai")
    title_text = (fig.layout.title.text or "").lower()
    assert "shanghai" in title_text
    assert "cnsha" in title_text


def test_regional_trend_figure_title_includes_region() -> None:
    series = [("2026-05-25", -1.0)]
    fig = build_regional_trend_figure(series, "Asia East")
    title_text = (fig.layout.title.text or "")
    assert "Asia East" in title_text


# ── 6. Severity-band band thresholds match the data model ────────────────


def test_severity_bands_cover_data_model_thresholds() -> None:
    """The shading thresholds must match the band boundaries documented
    in processing.port_supply_lines: < -10 critical, < -3 deficit,
    [-3, 3] balanced, [3, 10] surplus, > 10 heavy surplus."""
    labels = [b.label for b in SEVERITY_BANDS]
    assert labels == [
        "Critical Deficit", "Deficit", "Balanced",
        "Surplus", "Heavy Surplus",
    ]
    # Boundaries
    boundaries = [(b.y_low, b.y_high) for b in SEVERITY_BANDS]
    assert boundaries[0][1] == -10.0   # Critical upper
    assert boundaries[1] == (-10.0, -3.0)   # Deficit
    assert boundaries[2] == (-3.0, 3.0)     # Balanced
    assert boundaries[3] == (3.0, 10.0)     # Surplus
    assert boundaries[4][0] == 10.0    # Heavy Surplus lower
